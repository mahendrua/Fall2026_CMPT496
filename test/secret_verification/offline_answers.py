"""
@file offline_answers.py
@brief What the T-079 offline model answers, request by request (US-034).
@details Answers picks a reply from the structured-output schema a request
asks for and from what the request says (which file, directory, rule or
test it is about). The replies are valid for the pipeline's schemas and
deliberately repeat the planted secrets the way a careless model would:

    labelled       the admin password is <S01>; Password=<S08>; a PEM block
    vendor format  the Stripe, AWS and GitHub values
    no context     "... connects with <S08>", a test calling CanApprove("<S01>")

so the app's output cleaning (T-078) has something to clean. Failures are
scripted too: one file's first request gets a 503 (the app retries), one
file always gets a 400 whose message quotes secrets, and a phase set by the
harness makes later commands fail the same way.

Consistency Review answers quote the source as the model received it,
placeholders included, as a real model would (known gap B).
"""

import re
import threading
from pathlib import PurePath

from secret_fixture import PEM_BEGIN, PEM_END, make_secrets

# Phases the harness switches between (offline_model reads them per call).
NORMAL = "normal"
FAIL_DIRECTORY = "fail_directory_judgement"
FAIL_CONSISTENCY = "fail_consistency"


class ProviderError(Exception):
    """An error as a provider SDK raises it: the HTTP status on .status_code."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class UnexpectedRequest(Exception):
    """A request this scenario has no answer for: the harness reports it."""


def _find(pattern, text, group=1):
    match = re.search(pattern, text, re.M)
    return match.group(group).strip() if match else None


class Answers:
    """
    One run's answers. codebase is the fixture root as the pipeline sees it;
    paths in requests are matched against it.
    """

    def __init__(self, codebase, seed):
        self.codebase = str(codebase)
        self.v = {s.case_id: s.value for s in make_secrets(seed)}
        self._lock = threading.Lock()
        self._seen = {}

    # --- helpers ------------------------------------------

    def _count(self, key):
        """How many times this key was asked before (0 the first time)."""
        with self._lock:
            n = self._seen.get(key, 0)
            self._seen[key] = n + 1
            return n

    def _relative(self, path):
        """The path relative to the codebase root, POSIX style, or None."""
        if not path:
            return None
        path = path.strip().strip('"')
        root = self.codebase.rstrip("\\/")
        if path.replace("\\", "/").lower().startswith(root.replace("\\", "/").lower()):
            rest = path[len(root):].lstrip("\\/")
            return PurePath(rest.replace("\\", "/")).as_posix() if rest else "."
        return PurePath(path.replace("\\", "/")).as_posix()

    def answer(self, tool, text, phase=NORMAL):
        """The tool-call arguments for a request, or raise as a provider would."""
        handler = getattr(self, f"_{tool}", None) if tool else None
        if handler is None:
            raise UnexpectedRequest(f"no scripted answer for {tool or 'a plain text request'}")
        return handler(text, phase)

    # --- file summaries -----------------------------------

    def _FileSummaryOutput(self, text, phase):
        path = _find(r"^File path:\s*\n(.+)$", text)
        rel = self._relative(path)
        v = self.v

        if rel == "scripts/deploy.sh":
            raise ProviderError(400, (
                f"400 Bad Request: the request was rejected near 'GITHUB_TOKEN={v['S06']}' "
                f"(key {v['S05']}); retry without Authorization: Bearer {v['S07']}"
            ))

        if rel == "tools/report_export.py" and self._count("busy:" + rel) == 0:
            raise ProviderError(503, "503 Service Unavailable: the model is overloaded, try again")

        answer = self.summary(rel)
        if answer is None:
            raise UnexpectedRequest(f"no scripted summary for {rel}")
        return dict(answer, path=path)

    def summary(self, rel):
        """The scripted file summary for a fixture file (without its path), or None."""
        build = {
            "Billing/InvoiceService.cs": self._invoice_summary,
            "Integrations/PaymentClient.cs": self._payment_summary,
            "tools/report_export.py": self._report_summary,
            "web/session.js": self._session_summary,
            "docs/SETUP.md": self._setup_summary,
            "docs/architecture.md": self._architecture_summary,
        }.get(rel)
        return build() if build else None

    def _invoice_summary(self):
        v = self.v
        return {
            "summary": (
                f"Approves invoices above the 5000 threshold. The admin password is {v['S01']} "
                f"and the database connects with {v['S08']}. AdminPassword is a public setting."
            ),
            "dependencies": ["System"],
            "types": [
                {
                    "name": "InvoiceService",
                    "kind": "class",
                    "description": f"Checks approvals against AdminPassword ({v['S01']}). ApprovalThreshold = 5000m.",
                    "properties": [
                        {"name": "AdminPassword", "type": "string", "visibility": "private", "is_static": True,
                         "description": f"Hard-coded approval password {v['S01']}"},
                        {"name": "ApprovalThreshold", "type": "decimal", "visibility": "public", "is_static": True,
                         "description": "ApprovalThreshold = 5000m"},
                        {"name": "ConnectionString", "type": "string", "visibility": "public", "is_static": False,
                         "description": f"Server=db.ledger.internal;Database=Ledger;User Id=ledger_app;Password={v['S08']};"},
                    ],
                    "methods": [
                        {"name": "NeedsApproval", "description": "True when amount > ApprovalThreshold.",
                         "visibility": "public", "return_type": "bool",
                         "parameters": [{"name": "amount", "type": "decimal", "direction": "in"}]},
                        {"name": "CanApprove", "description": "Compares the input with AdminPassword.",
                         "visibility": "public", "return_type": "bool",
                         "parameters": [{"name": "input", "type": "string", "direction": "in"}]},
                        {"name": "PrimaryKeyFor", "description": 'Builds the key from var primaryKey = "InvoiceId";',
                         "visibility": "public", "return_type": "string",
                         "parameters": [{"name": "invoice", "type": "Invoice", "direction": "in"}]},
                    ],
                    "plantuml": (
                        "class InvoiceService {\n"
                        f'  -password : string = "{v["S01"]}"\n'
                        "  -approvalThreshold : decimal = 5000\n"
                        '  -cacheKey : string = "invoice-cache-v2"\n'
                        "  +NeedsApproval(in amount : decimal) : bool\n"
                        "  +CanApprove(in input : string) : bool\n"
                        "}"
                    ),
                },
                {
                    "name": "Invoice",
                    "kind": "class",
                    "description": "An invoice with an Id and an Amount.",
                    "properties": [
                        {"name": "Id", "type": "int", "visibility": "public", "is_static": False, "description": "Invoice number."},
                        {"name": "Amount", "type": "decimal", "visibility": "public", "is_static": False, "description": "Amount due."},
                    ],
                    "plantuml": "class Invoice {\n  +Id : int\n  +Amount : decimal\n}",
                },
            ],
            "relationships": [{"source": "InvoiceService", "target": "Invoice", "relationship_type": "dependency"}],
            "relationship_plantuml": "@startuml\nclass InvoiceService\nclass Invoice\nInvoiceService ..> Invoice\n@enduml",
            "business_rules": [
                {"rule": "Invoices above 5000 CAD need manager approval."},
                {"rule": f"An invoice can be approved only with the admin password {v['S01']}."},
            ],
        }

    def _payment_summary(self):
        v = self.v
        return {
            "summary": (
                f"Charges payments with the key {v['S04']} and sends Authorization: Bearer {v['S07']}. "
                "Payments above 10000 are large. Docs: https://docs.example.com/ledger/api"
            ),
            "dependencies": ["System.Net.Http", "System.Net.Http.Headers"],
            "types": [{
                "name": "PaymentClient",
                "kind": "class",
                "description": f"Payment provider client; its default header carries {v['S07']}.",
                "properties": [
                    {"name": "StripeKey", "type": "string", "visibility": "private", "is_static": True,
                     "description": f"The secret key {v['S04']}"},
                    {"name": "DocsUrl", "type": "string", "visibility": "public", "is_static": True,
                     "description": "https://docs.example.com/ledger/api"},
                ],
                "methods": [
                    {"name": "PaymentClient", "description": "Sets the Authorization header.", "visibility": "public",
                     "is_constructor": True, "parameters": []},
                    {"name": "IsLargePayment", "description": "amount >= 10000m", "visibility": "public",
                     "return_type": "bool", "parameters": [{"name": "amount", "type": "decimal", "direction": "in"}]},
                ],
                "plantuml": (
                    "class PaymentClient {\n  -stripeKey : string\n  +DocsUrl : string\n"
                    "  +PaymentClient()\n  +IsLargePayment(in amount : decimal) : bool\n}"
                ),
            }],
            "relationship_plantuml": "@startuml\nclass PaymentClient\n@enduml",
            "business_rules": [{"rule": "Payments of 10000 or more are treated as large payments."}],
        }

    def _report_summary(self):
        v = self.v
        return {
            "summary": (
                f"Exports monthly reports from postgresql://report_user:{v['S09']}@reports.ledger.internal:5432/ledger "
                f"with the API key {v['S02']}; it reads os.getenv(\"REPORTING_API_KEY\") first and writes to "
                "C:\\LedgerLite\\exports."
            ),
            "dependencies": ["os"],
            "functions": [
                {"name": "api_key", "description": f"Returns the key, {v['S02']} by default.", "parameters": [], "calls": ["os.getenv"]},
                {"name": "export_headers", "description": "Builds the X-Api-Key header.", "parameters": [], "calls": ["api_key"]},
            ],
            "business_rules": [{"rule": "A monthly export holds at most 10000 rows."}],
        }

    def _session_summary(self):
        v = self.v
        return {
            "summary": (
                f"Keeps a web session with refreshToken {v['S03']} and tokenType \"Bearer\"; "
                "primaryKey is ledgerId and the API key comes from process.env.LEDGER_API_KEY."
            ),
            "functions": [
                {"name": "apiKey", "description": "Reads process.env.LEDGER_API_KEY.", "parameters": []},
                {"name": "refreshUrl", "description": f"Returns the refresh URL ending in refresh_token={v['S03']}.", "parameters": []},
            ],
            "business_rules": [{"rule": "A web session expires after 3600 seconds."}],
        }

    def _setup_summary(self):
        v = self.v
        return {
            "summary": (
                f"Setup guide. The default admin password is {v['S01']}. "
                "Invoices above 5000 CAD need manager approval. Set REPORTING_API_KEY=YOUR_API_KEY. "
                f"The webhook signing key is:\n{PEM_BEGIN}\n{v['S10']}\n{PEM_END}"
            ),
            "business_rules": [{"rule": "Invoices above 5000 CAD need manager approval before they are paid."}],
        }

    def _architecture_summary(self):
        v = self.v
        return {
            "summary": f'Describes InvoiceService in PlantUML, with the field -password : string = "{v["S01"]}".',
            "types": [{
                "name": "InvoiceService",
                "kind": "class",
                "description": "The approval service as drawn in the architecture notes.",
                "plantuml": (
                    "class InvoiceService {\n"
                    f'  -password : string = "{v["S01"]}"\n'
                    "  -approvalThreshold : decimal = 5000\n"
                    "  +ApproveInvoice(invoice : Invoice) : bool\n"
                    "}"
                ),
            }],
        }

    # --- directory summaries ------------------------------

    _PURPOSES = {
        "Billing": "Invoice approval. Approval needs the admin password {S01}; invoices above 5000 CAD need manager approval.",
        "Integrations": "Payment provider client; it authenticates with Bearer {S07}.",
        "tools": "Report export, using the API key {S02}.",
        "web": "Web session handling with the refresh token {S03}.",
        "scripts": "Deployment scripts exporting AWS_ACCESS_KEY_ID={S05}.",
        "docs": "Setup and architecture documentation; see https://docs.example.com/ledger/api.",
        ".": "LedgerLite: invoices, payments, reports and deployment.",
    }

    def _directory(self, text):
        path = (
            _find(r"(?:ROOT )?DIRECTORY TO ANALYZE:\s*(.+)$", text)
            or _find(r"^\s*DIRECTORY:\s*(\S.*)$", text)
            or _find(r"^\s*DIRECTORY:\s*\n\s*(\S.*)$", text)
        )
        rel = self._relative(path) or "."
        return path or self.codebase, rel

    def _ContextAnalysisOutput(self, text, phase):
        return {"sufficient_code_context": True, "sufficient_summary_context": True}

    def _DirectoryOutput(self, text, phase):
        path, rel = self._directory(text)
        if phase == FAIL_DIRECTORY:
            # The summarizer catches this, logs it and saves it as the purpose.
            raise ProviderError(400, (
                "400 Bad Request: could not read 'DATABASE_URL = "
                f"\"postgresql://report_user:{self.v['S09']}@reports.ledger.internal:5432/ledger\"'"
            ))
        purpose = self._PURPOSES.get(rel, "Supporting files.").format(**self.v)
        return {
            "directory_name": PurePath(path).name,
            "directory_path": path,
            "purpose": purpose,
            "responsibilities": [f"Owns {rel}", "Keeps ApprovalThreshold = 5000m" if rel == "Billing" else "Support"],
        }

    def _JudgementOutput(self, text, phase):
        path, rel = self._directory(text)
        if phase == FAIL_DIRECTORY:
            raise ProviderError(400, (
                f"400 Bad Request: content policy rejected 'Password={self.v['S08']};' "
                f"and the token {self.v['S06']}"
            ))
        # Billing is sent back once, so the refinement path runs too.
        if rel == "Billing" and self._count("judge:" + rel) == 0:
            return {"summary_acceptable": False, "feedback": f"Say why {self.v['S01']} matters for approval."}
        return {"summary_acceptable": True, "feedback": None}

    def _BusinessRulesOutput(self, text, phase):
        path, rel = self._directory(text)
        observed = {
            "Billing": ["Invoices above 5000 CAD need manager approval."],
            "Integrations": [f"Payment calls authenticate with the bearer token {self.v['S07']}."],
            "web": ["A web session expires after 3600 seconds."],
        }.get(rel, [])
        return {"observed_rules": observed, "inferred_rules": []}

    # --- business rules -----------------------------------

    def _CondenserOutput(self, text, phase):
        numbered = re.findall(r"^(\d+)\.\s+(.+)$", text.split("Business rules to condense:", 1)[-1].split("MERGING GUIDELINES", 1)[0], re.M)
        if not numbered:
            raise UnexpectedRequest("no numbered rules in the condenser request")
        return {"condensed_rules": [{"rule": rule.strip(), "source_rule_numbers": [int(n)]} for n, rule in numbered]}

    def _ValidatorOutput(self, text, phase):
        rule = _find(r"^Rule:\s*(.+)$", text) or ""
        v = self.v
        lowered = rule.lower()

        if "5000" in rule and "approval" in lowered and "paid" not in lowered:
            return {"decision": "valid", "explanation": {
                "evidence": {"Billing/InvoiceService.cs": [
                    "public const decimal ApprovalThreshold = 5000m;",
                    "public bool NeedsApproval(decimal amount) => amount > ApprovalThreshold;",
                ]},
                "reasoning": "NeedsApproval compares the amount with ApprovalThreshold = 5000m.",
            }}
        if "admin password" in lowered:
            return {"decision": "valid", "explanation": {
                "evidence": {"Billing/InvoiceService.cs": [
                    f'private const string AdminPassword = "{v["S01"]}";',
                    "public bool CanApprove(string input) => input == AdminPassword;",
                ]},
                "reasoning": f"CanApprove only accepts {v['S01']}.",
            }}
        if "10000" in rule and "payment" in lowered:
            return {"decision": "valid", "explanation": {
                "evidence": {"Integrations/PaymentClient.cs": ["public bool IsLargePayment(decimal amount) => amount >= 10000m;"]},
                "reasoning": f"IsLargePayment uses amount >= 10000m; charges use {v['S04']}.",
            }}
        return {"decision": "discard", "explanation": None,
                "discard_reason": "Not enforced by the C# code that was retrieved."}

    # --- generated tests ----------------------------------

    def _UnitTest(self, text, phase):
        rule_id = int(_find(r"- ID:\s*(\d+)", text) or 0)
        rule = _find(r"- RULE STATEMENT:\s*(.+)$", text) or ""
        lowered = rule.lower()
        v = self.v

        if "password" in lowered:
            # A test that hard-codes a secret: saved, it holds a placeholder,
            # so it compiles and then fails, which the report shows.
            return self._unit(rule_id, rule, "Billing", ["using Xunit;", "using LedgerLite.Billing;"], (
                "[Fact]\n"
                "public void Approval_needs_the_admin_password()\n"
                "{\n"
                "    var service = new InvoiceService();\n"
                f'    Assert.True(service.CanApprove("{v["S01"]}"));\n'
                '    Assert.False(service.CanApprove("wrong-password"));\n'
                "}"
            ))
        if "10000" in rule:
            # Does not compile (no IsLarge), so it goes to the AI for repair
            # with a context-free echo inside.
            return self._unit(rule_id, rule, "Integrations", ["using Xunit;", "using LedgerLite.Integrations;"], (
                "[Fact]\n"
                "public void Large_payments_start_at_10000()\n"
                "{\n"
                f"    // Charges are made with {v['S08']}.\n"
                "    var client = new PaymentClient();\n"
                "    Assert.True(client.IsLarge(10000m));\n"
                "}"
            ))
        return self._unit(rule_id, rule, "Billing", ["using Xunit;", "using LedgerLite.Billing;"], (
            "[Fact]\n"
            "public void Invoices_above_5000_need_approval()\n"
            "{\n"
            "    var service = new InvoiceService();\n"
            "    Assert.True(service.NeedsApproval(5000.01m));\n"
            "    Assert.False(service.NeedsApproval(5000m));\n"
            "}"
        ))

    def _unit(self, rule_id, rule, directory, imports, body):
        files = {"Billing": ["Billing/InvoiceService.cs"], "Integrations": ["Integrations/PaymentClient.cs"]}[directory]
        return {"id": rule_id, "rule": rule, "imports": imports, "source_directory": directory,
                "source_file_paths": files, "unit_test": body}

    def _TestRepair(self, text, phase):
        # Only the payment test is broken on purpose; anything else is given up on.
        if "IsLarge(" not in text:
            return {"imports": [], "test_method": ""}
        return {"imports": ["using Xunit;", "using LedgerLite.Integrations;"], "test_method": (
            "[Fact]\n"
            "public void Large_payments_start_at_10000()\n"
            "{\n"
            "    var client = new PaymentClient();\n"
            "    Assert.True(client.IsLargePayment(10000m));\n"
            "    Assert.False(client.IsLargePayment(9999.99m));\n"
            "}"
        )}

    def _WorkflowGroups(self, text, phase):
        ids = sorted({int(n) for n in re.findall(r"Rule ID:\s*\n\s*(\d+)", text)})
        if not ids:
            raise UnexpectedRequest("no rule IDs in the workflow request")
        return {"workflows": [{
            "workflow_name": "Approve and pay a large invoice",
            "workflow_description": "A large invoice needs approval and is charged as a large payment.",
            "rule_ids": ids,
        }]}

    def _IntegrationTest(self, text, phase):
        name = _find(r"Workflow Name:\s*\n\s*(.+)$", text) or "Workflow"
        return {
            "workflow_name": name,
            "workflow_description": "A large invoice needs approval and is charged as a large payment.",
            "rule_ids": sorted({int(n) for n in re.findall(r"Rule ID:\s*\n\s*(\d+)", text)}),
            "imports": ["using Xunit;", "using LedgerLite.Billing;", "using LedgerLite.Integrations;"],
            "integration_test": (
                "[Fact]\n"
                "public void Large_invoice_needs_approval_and_is_a_large_payment()\n"
                "{\n"
                "    var invoices = new InvoiceService();\n"
                "    var payments = new PaymentClient();\n"
                f"    // Charged with the payments key {self.v['S04']}.\n"
                "    Assert.True(invoices.NeedsApproval(12000m));\n"
                "    Assert.True(payments.IsLargePayment(12000m));\n"
                "}"
            ),
        }

    # --- Consistency Review -------------------------------

    def _ConsistencyAnalysisOutput(self, text, phase):
        if phase == FAIL_CONSISTENCY:
            raise ProviderError(400, f"400 Bad Request: could not parse 'DATABASE_URL = \"postgresql://report_user:{self.v['S09']}@reports\"'")

        file = _find(r"^File:\s*(.+)$", text) or ""
        handles = [h.strip() for h in (_find(r"^Generated artifacts to assess:\s*(.+)$", text) or "").split(",") if h.strip()]
        source = dict(
            (int(n), line) for n, line in re.findall(r"^\s*(\d+) \| ?(.*)$", text, re.M)
        )

        def line_with(fragment):
            for number, line in sorted(source.items()):
                if fragment in line:
                    return number, line.strip()
            return None, None

        verdicts = {h: "consistent" for h in handles}
        findings = []

        if file == "Billing/InvoiceService.cs" and "SUMMARY" in handles:
            # The model only saw the placeholder, so it quotes the line with it
            # (gap B), and a second, made-up line that is in no version of the file.
            number, line = line_with("AdminPassword =")
            if number:
                findings.append(self._finding(
                    "source_summary", "AdminPassword is private, not a public setting",
                    line, number, "SUMMARY", "AdminPassword is a public setting.",
                ))
                findings.append(self._finding(
                    "source_summary", "The summary describes a different password constant",
                    'private const string AdminPassword = "letmein";', number, "SUMMARY",
                    "AdminPassword is a public setting.",
                ))
                verdicts["SUMMARY"] = "inconsistent"

        if file == "Integrations/PaymentClient.cs" and "SUMMARY" in handles:
            number, line = line_with("IsLargePayment(decimal amount)")
            if number:
                findings.append(self._finding(
                    "source_summary", "10000 itself is already a large payment",
                    line, number, "SUMMARY", "Payments above 10000 are large.",
                ))
                verdicts["SUMMARY"] = "inconsistent"

        return {
            "assessments": [
                {"artifact": h, "verdict": verdict,
                 "reason": "The artifact matches what the code does." if verdict == "consistent"
                 else "The code and the artifact disagree; see the finding."}
                for h, verdict in verdicts.items()
            ],
            "findings": findings,
        }

    @staticmethod
    def _finding(category, title, excerpt, line, artifact, claim):
        return {
            "category": category,
            "title": title,
            "explanation": f"The code says {excerpt.strip()!s}; the artifact says {claim}",
            "source": {"statement": "What the code does at that line.", "excerpt": excerpt,
                       "line_start": line, "line_end": line},
            "claims": [{"artifact": artifact, "statement": claim, "excerpt": claim}],
            "behaviour_uncertainty": [],
        }
