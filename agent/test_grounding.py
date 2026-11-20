
from types import SimpleNamespace

import pytest

from agent.file_summary_grounding import (
    collect_source_declarations,
    validate_file_summary_against_source,
    filter_business_rules_against_source,
)



# ---------------------------------------------------------
# Declaration collector tests
# ---------------------------------------------------------

def test_python_collector_finds_classes_methods_and_functions():
    source = """
class Account:
    def deposit(self):
        pass

    def withdraw(self):
        pass

def create_account():
    pass
"""

    declarations = collect_source_declarations(source, "python")

    assert declarations is not None
    assert declarations.parse_error is False

    assert "Account" in declarations.types
    assert declarations.types["Account"] == {"deposit", "withdraw"}

    assert "create_account" in declarations.functions


def test_python_collector_detects_syntax_errors():
    source = """
class Account
    def deposit(self):
        pass
"""

    declarations = collect_source_declarations(source, "python")

    assert declarations is not None
    assert declarations.parse_error is True


def test_unsupported_language_returns_none():
    declarations = collect_source_declarations(
        "public class Account {}",
        "java",
    )

    assert declarations is None


# ---------------------------------------------------------
# File summary filtering tests
# ---------------------------------------------------------

def test_file_summary_keeps_valid_declarations():
    source = """
class Account:
    def deposit(self):
        pass

def create_account():
    pass
"""

    summary = SimpleNamespace(
        types=[
            SimpleNamespace(
                name="Account",
                methods=[
                    SimpleNamespace(name="deposit"),
                ],
            )
        ],
        functions=[
            SimpleNamespace(name="create_account"),
        ],
    )

    result = validate_file_summary_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.types) == 1
    assert result.types[0].name == "Account"
    assert [m.name for m in result.types[0].methods] == ["deposit"]

    assert [f.name for f in result.functions] == ["create_account"]


def test_file_summary_removes_invented_declarations():
    source = """
class Account:
    def deposit(self):
        pass

def create_account():
    pass
"""

    summary = SimpleNamespace(
        types=[
            SimpleNamespace(
                name="Account",
                methods=[
                    SimpleNamespace(name="deposit"),
                    SimpleNamespace(name="transfer"),
                ],
            ),
            SimpleNamespace(
                name="PaymentProcessor",
                methods=[],
            ),
        ],
        functions=[
            SimpleNamespace(name="create_account"),
            SimpleNamespace(name="delete_everything"),
        ],
    )

    result = validate_file_summary_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.types) == 1
    assert result.types[0].name == "Account"

    assert [m.name for m in result.types[0].methods] == ["deposit"]
    assert [f.name for f in result.functions] == ["create_account"]


def test_file_summary_skips_filtering_on_syntax_error():
    source = """
class Account
    def deposit(self):
        pass
"""

    summary = SimpleNamespace(
        types=[
            SimpleNamespace(
                name="InventedClass",
                methods=[],
            )
        ],
        functions=[
            SimpleNamespace(name="invented_function"),
        ],
    )

    result = validate_file_summary_against_source(
        summary,
        "account.py",
        source,
    )

    # Nothing should be removed when parsing fails.
    assert len(result.types) == 1
    assert result.types[0].name == "InventedClass"
    assert len(result.functions) == 1


def test_file_summary_skips_unsupported_language():
    summary = SimpleNamespace(
        types=[
            SimpleNamespace(name="Account", methods=[]),
        ],
        functions=[],
    )

    result = validate_file_summary_against_source(
        summary,
        "Account.java",
        "public class Account {}",
    )

    assert len(result.types) == 1
    assert result.types[0].name == "Account"


# ---------------------------------------------------------
# G1 business-rule filtering tests
# ---------------------------------------------------------

def test_business_rule_filter_keeps_declared_method_calls():
    source = """
class Account:
    def deposit(self):
        pass
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.deposit() to add funds."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.business_rules) == 1


def test_business_rule_filter_removes_undeclared_method_calls():
    source = """
class Account:
    def deposit(self):
        pass
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.business_rules) == 0


def test_business_rule_filter_keeps_rules_on_syntax_error():
    source = """
class Account
    def deposit(self):
        pass
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.business_rules) == 1


def test_business_rule_filter_skips_unsupported_language():
    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw()."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "Account.java",
        "public class Account {}",
    )

    assert len(result.business_rules) == 1


def test_nested_classes_keep_methods_under_correct_owner():
    source = """
class Outer:
    def outer_method(self):
        pass

    class Inner:
        def inner_method(self):
            pass
"""

    summary = SimpleNamespace(
        types=[
            SimpleNamespace(
                name="Outer",
                methods=[
                    SimpleNamespace(name="outer_method"),
                    SimpleNamespace(name="inner_method"),
                ],
            ),
            SimpleNamespace(
                name="Outer.Inner",
                methods=[
                    SimpleNamespace(name="inner_method"),
                ],
            ),
        ],
        functions=[],
    )

    result = validate_file_summary_against_source(
        summary,
        "nested.py",
        source,
    )

    outer = next(t for t in result.types if t.name == "Outer")
    inner = next(t for t in result.types if t.name == "Outer.Inner")

    assert [m.name for m in outer.methods] == ["outer_method"]
    assert [m.name for m in inner.methods] == ["inner_method"]


def test_business_rule_filter_keeps_dynamically_attached_methods():
    source = """
class Account:
    pass

def withdraw(self):
    pass

Account.withdraw = withdraw
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.business_rules) == 1


def test_business_rule_filter_keeps_uncertain_inherited_methods():
    source = """
class BaseAccount:
    def withdraw(self):
        pass

class Account(BaseAccount):
    pass
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.business_rules) == 1


def test_business_rule_filter_keeps_uncertain_decorated_class_methods():
    source = """
def register(cls):
    return cls

@register
class Account:
    pass
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            )
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.py",
        source,
    )

    assert len(result.business_rules) == 1

def test_javascript_collector_finds_classes_methods_and_functions():
    source = """
function calculateTotal(items) {
    return items.length;
}

const formatResult = (value) => String(value);

class Account {
    constructor(balance) {
        this.balance = balance;
    }

    withdraw(amount) {
        this.balance -= amount;
    }

    static create() {
        return new Account(0);
    }
}
"""

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert "Account" in declarations.types
    assert declarations.types["Account"] == {
        "constructor",
        "withdraw",
        "create",
    }

    assert "calculateTotal" in declarations.functions
    assert "formatResult" in declarations.functions


def test_javascript_collector_detects_inherited_classes():
    source = """
class BaseAccount {
    withdraw(amount) {}
}

class Account extends BaseAccount {
    deposit(amount) {}
}
"""

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert "Account" in declarations.types
    assert "Account" in declarations.open_types


def test_javascript_collector_detects_syntax_errors():
    source = """
class Account {
    withdraw(
}
"""

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is True


def test_javascript_file_summary_removes_invented_declarations():
    source = """
class Account {
    withdraw(amount) {}
}
"""

    summary = SimpleNamespace(
        types=[
            SimpleNamespace(
                name="Account",
                methods=[
                    SimpleNamespace(name="withdraw"),
                    SimpleNamespace(name="deposit"),
                ],
            )
        ],
        functions=[],
    )

    result = validate_file_summary_against_source(
        summary, "account.js", source
    )

    assert [method.name for method in result.types[0].methods] == [
        "withdraw"
    ]


def test_javascript_business_rule_filter_removes_undeclared_method_calls():
    source = """
    class Account {
        withdraw(amount) {}
    }
    """

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            ),
            SimpleNamespace(
                rule="The system calls Account.deposit() to add funds."
            ),
        ]
    )

    result = filter_business_rules_against_source(
        summary, "account.js", source
    )

    assert len(result.business_rules) == 1
    assert "withdraw" in result.business_rules[0].rule



def test_javascript_collector_finds_nested_classes():
    source = """
class AccountManager {
    createAccount() {
        class Account {
            deposit(amount) {}
            withdraw(amount) {}
        }

        return new Account();
    }
}
"""

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert "AccountManager" in declarations.types
    assert "AccountManager.Account" in declarations.types

    assert declarations.types["AccountManager"] == {"createAccount"}
    assert declarations.types["AccountManager.Account"] == {
        "deposit",
        "withdraw",
    }


def test_javascript_business_rule_filter_keeps_dynamically_assigned_methods():
    source = """
class Account {}

Account.prototype.withdraw = function(amount) {
    return amount;
};

Account.create = function() {
    return new Account();
};
"""

    summary = SimpleNamespace(
        business_rules=[
            SimpleNamespace(
                rule="The system calls Account.withdraw() to remove funds."
            ),
            SimpleNamespace(
                rule="The system calls Account.create() to create an account."
            ),
            SimpleNamespace(
                rule="The system calls Account.deposit() to add funds."
            ),
        ]
    )

    result = filter_business_rules_against_source(
        summary,
        "account.js",
        source,
    )

    assert len(result.business_rules) == 2

    rules = [rule.rule for rule in result.business_rules]

    assert any("Account.withdraw()" in rule for rule in rules)
    assert any("Account.create()" in rule for rule in rules)
    assert not any("Account.deposit()" in rule for rule in rules)


def test_javascript_collector_finds_named_and_anonymous_class_expressions():
    source = """
const Account = class {
    deposit(amount) {}
    withdraw(amount) {}
};

const PaymentProcessor = class InternalProcessor {
    processPayment(payment) {}
    refundPayment(payment) {}
};
"""

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert "Account" in declarations.types
    assert declarations.types["Account"] == {
        "deposit",
        "withdraw",
    }

    assert "PaymentProcessor" in declarations.types
    assert declarations.types["PaymentProcessor"] == {
        "processPayment",
        "refundPayment",
    }


def test_javascript_collector_finds_exported_declarations():
    source = """
    export function calculateTotal(items) {
        return items.length;
    }

    export class Account {
        withdraw(amount) {}
    }

    export const formatResult = (value) => String(value);

    export default class PaymentProcessor {
        processPayment(payment) {}
    }
    """

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert "calculateTotal" in declarations.functions
    assert "formatResult" in declarations.functions

    assert declarations.types["Account"] == {"withdraw"}
    assert declarations.types["PaymentProcessor"] == {"processPayment"}


def test_javascript_collector_finds_async_declarations():
    source = """
    async function fetchAccount(id) {
        return id;
    }

    class AccountService {
        async loadAccount(id) {}
        async saveAccount(account) {}
    }

    const refreshAccount = async (id) => {
        return id;
    };
    """

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert "fetchAccount" in declarations.functions
    assert "refreshAccount" in declarations.functions

    assert declarations.types["AccountService"] == {
        "loadAccount",
        "saveAccount",
    }


def test_javascript_collector_finds_getters_and_setters():
    source = """
    class Account {
        get balance() {
            return this._balance;
        }

        set balance(value) {
            this._balance = value;
        }

        get owner() {
            return this._owner;
        }

        set owner(value) {
            this._owner = value;
        }
    }
    """

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert declarations.types["Account"] == {
        "balance",
        "owner",
    }




def test_javascript_collector_does_not_misclassify_object_methods():
    source = """
    const account = {
        deposit(amount) {},
        withdraw(amount) {},
        async transfer(amount) {}
    };
    """

    declarations = collect_source_declarations(source, "javascript")

    assert declarations is not None
    assert declarations.parse_error is False

    assert declarations.types == {}
    assert declarations.functions == set()