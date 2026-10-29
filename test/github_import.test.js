const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const yazl = require("yazl");
const {
    cleanupIncompleteImports,
    getPullRequestDetails,
    getPullRequestFileComparison,
    getRepositoryBranches,
    importRepository,
    listPullRequests,
    parseRepositoryUrl,
    validateBranchName
} = require("../github_import");

function createTemporaryDirectory(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "checkpoint-github-"));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    return directory;
}

function jsonResponse(value, { status = 200, headers = {} } = {}) {
    return new Response(JSON.stringify(value), { status, headers });
}

function createZip(entries) {
    return new Promise((resolve, reject) => {
        const zip = new yazl.ZipFile();
        const chunks = [];
        zip.outputStream.on("data", chunk => chunks.push(chunk));
        zip.outputStream.on("error", reject);
        zip.outputStream.on("end", () => resolve(Buffer.concat(chunks)));
        for (const entry of entries) {
            zip.addBuffer(Buffer.from(entry.contents ?? "file contents"), entry.name, entry.options);
        }
        zip.end();
    });
}

test("parses standard, trailing-slash, and .git repository URLs", () => {
    assert.deepEqual(parseRepositoryUrl("https://github.com/owner/repository"), {
        owner: "owner",
        repo: "repository",
        url: "https://github.com/owner/repository"
    });
    assert.equal(parseRepositoryUrl("https://github.com/owner/repository/").repo, "repository");
    assert.equal(parseRepositoryUrl("https://github.com/owner/repository.git").repo, "repository");
});

test("rejects non-GitHub, non-HTTPS, credentialed, and malformed repository URLs", () => {
    for (const value of [
        "http://github.com/owner/repo",
        "https://evil.example/owner/repo",
        "https://user@github.com/owner/repo",
        "https://github.com/owner/repo?tab=readme",
        "https://github.com/owner",
        "https://github.com/owner/../repo"
    ]) {
        assert.throws(() => parseRepositoryUrl(value), /GitHub repository URL|public repositories|owner and repository/);
    }
});

test("accepts valid branch names and rejects Git-ref syntax hazards", () => {
    assert.equal(validateBranchName("feature/add-import"), "feature/add-import");
    for (const branch of ["", "../main", "-option", "main.lock", "release//v1", "name with spaces"]) {
        assert.throws(() => validateBranchName(branch), /branch name is invalid/);
    }
});

test("lists paginated branches with the default branch first", async () => {
    const requests = [];
    const branches = await getRepositoryBranches("https://github.com/owner/repo", {
        fetchImpl: async url => {
            requests.push(url);
            if (url.endsWith("/repos/owner/repo")) return jsonResponse({ default_branch: "main" });
            if (url.endsWith("page=1")) {
                return jsonResponse([{ name: "z-feature" }, { name: "main" }], {
                    headers: { link: '<https://api.github.com/repos/owner/repo/branches?per_page=100&page=2>; rel="next"' }
                });
            }
            return jsonResponse([{ name: "a-feature" }, { name: "main" }]);
        }
    });

    assert.equal(requests.length, 3);
    assert.deepEqual(branches.branches, ["main", "a-feature", "z-feature"]);
    assert.equal(branches.defaultBranch, "main");
});

test("explains missing/private repository responses", async () => {
    await assert.rejects(
        getRepositoryBranches("https://github.com/owner/private", {
            fetchImpl: async () => jsonResponse({}, { status: 404 })
        }),
        /not found or is private/
    );
});

test("rejects repeated branch pagination links", async () => {
    const firstPage = "https://api.github.com/repos/owner/repo/branches?per_page=100&page=1";
    await assert.rejects(getRepositoryBranches("https://github.com/owner/repo", {
        fetchImpl: async url => url.endsWith("/repos/owner/repo")
            ? jsonResponse({ default_branch: "main" })
            : jsonResponse([{ name: "main" }], {
                headers: { link: `<${firstPage}>; rel="next"` }
            })
    }), /repeated branch pagination link/);
});

test("lists pull requests for a target branch and preserves pagination metadata", async () => {
    const requests = [];
    const result = await listPullRequests("https://github.com/owner/repo", "main", {
        state: "all",
        page: 2,
        fetchImpl: async url => {
            requests.push(url);
            return jsonResponse([{
                number: 12,
                title: "Improve checks",
                state: "closed",
                merged_at: "2026-04-01T10:00:00Z",
                user: { login: "contributor" },
                base: { ref: "main", sha: "a".repeat(40) },
                head: { ref: "feature/checks", sha: "b".repeat(40) }
            }], {
                headers: { link: '<https://api.github.com/repos/owner/repo/pulls?page=3>; rel="next"' }
            });
        }
    });

    assert.match(requests[0], /state=all/);
    assert.match(requests[0], /base=main/);
    assert.match(requests[0], /page=2/);
    assert.equal(result.hasMore, true);
    assert.equal(result.pullRequests[0].state, "merged");
    assert.equal(result.pullRequests[0].author, "contributor");
});

test("loads pull request commits and changed files with completeness indicators", async () => {
    const requests = [];
    const baseSha = "c".repeat(40);
    const headSha = "d".repeat(40);
    const result = await getPullRequestDetails("https://github.com/owner/repo", 7, {
        fetchImpl: async url => {
            requests.push(url);
            if (url.endsWith("/pulls/7")) {
                return jsonResponse({
                    number: 7,
                    title: "Add a feature",
                    state: "closed",
                    merged_at: "2026-04-02T10:00:00Z",
                    base: { ref: "main", sha: baseSha, repo: { full_name: "owner/repo" } },
                    head: { ref: "feature", sha: headSha }
                });
            }
            if (url.includes("/pulls/7/commits")) {
                return jsonResponse([{ sha: headSha, commit: { message: "Add feature", author: { name: "Contributor", date: "2026-04-01T10:00:00Z" } } }]);
            }
            if (url.includes("/pulls/7/files")) {
                return jsonResponse([{ filename: "src/app.js", status: "modified", additions: 2, deletions: 1 }]);
            }
            throw new Error(`Unexpected request: ${url}`);
        }
    });

    assert.equal(requests.length, 3);
    assert.equal(result.baseSha, baseSha);
    assert.equal(result.headSha, headSha);
    assert.equal(result.commits[0].message, "Add feature");
    assert.equal(result.files[0].filename, "src/app.js");
    assert.equal(result.dataComplete, true);
});

test("compares changed file content against immutable base and head commit SHAs", async () => {
    const baseSha = "e".repeat(40);
    const headSha = "f".repeat(40);
    const requests = [];
    const comparison = await getPullRequestFileComparison(
        "https://github.com/owner/repo",
        9,
        "src/app.js",
        {
            fetchImpl: async url => {
                requests.push(url);
                if (url.endsWith("/pulls/9")) {
                    return jsonResponse({
                        number: 9,
                        title: "Update app",
                        state: "closed",
                        merged_at: "2026-04-03T10:00:00Z",
                        base: { ref: "main", sha: baseSha },
                        head: { ref: "feature", sha: headSha }
                    });
                }
                if (url.includes("/pulls/9/files")) {
                    return jsonResponse([{ filename: "src/app.js", status: "modified", additions: 1, deletions: 1 }]);
                }
                if (url.includes(`?ref=${baseSha}`)) {
                    return jsonResponse({
                        type: "file",
                        encoding: "base64",
                        content: Buffer.from("before").toString("base64")
                    });
                }
                if (url.includes(`?ref=${headSha}`)) {
                    return jsonResponse({
                        type: "file",
                        encoding: "base64",
                        content: Buffer.from("after").toString("base64")
                    });
                }
                throw new Error(`Unexpected request: ${url}`);
            }
        }
    );

    assert.ok(requests.some(url => url.includes(`?ref=${baseSha}`)));
    assert.ok(requests.some(url => url.includes(`?ref=${headSha}`)));
    assert.equal(comparison.comparisonKind, "original-pull-request");
    assert.equal(comparison.before.text, "before");
    assert.equal(comparison.after.text, "after");
});

test("represents added, deleted, renamed, binary, and unsupported files accurately", async () => {
    const baseSha = "1".repeat(40);
    const headSha = "2".repeat(40);
    async function compareFile({ filename, previousFilename = null, before, after, selectedPath = filename }) {
        return getPullRequestFileComparison("https://github.com/owner/repo", 10, selectedPath, {
            fetchImpl: async url => {
                if (url.endsWith("/pulls/10")) {
                    return jsonResponse({
                        number: 10,
                        title: "File changes",
                        state: "closed",
                        base: { ref: "main", sha: baseSha },
                        head: { ref: "feature", sha: headSha }
                    });
                }
                if (url.includes("/pulls/10/files")) {
                    return jsonResponse([{
                        filename,
                        previous_filename: previousFilename,
                        status: previousFilename ? "renamed" : "modified",
                        additions: 1,
                        deletions: 1
                    }]);
                }
                const request = new URL(url);
                const commit = request.searchParams.get("ref");
                const requestedPath = decodeURIComponent(request.pathname.split("/contents/")[1] || "");
                const expectedPath = commit === baseSha ? previousFilename || filename : filename;
                assert.equal(requestedPath, expectedPath);
                const contents = commit === baseSha ? before : after;
                if (contents === undefined) return new Response("", { status: 404 });
                if (contents === "unsupported") {
                    return jsonResponse({ type: "file", encoding: "none" });
                }
                const bytes = Buffer.isBuffer(contents) ? contents : Buffer.from(contents);
                return jsonResponse({
                    type: "file",
                    encoding: "base64",
                    content: bytes.toString("base64")
                });
            }
        });
    }

    const added = await compareFile({ filename: "new.js", before: undefined, after: "new contents" });
    assert.equal(added.before.exists, false);
    assert.equal(added.after.text, "new contents");

    const deleted = await compareFile({ filename: "old.js", before: "old contents", after: undefined });
    assert.equal(deleted.before.text, "old contents");
    assert.equal(deleted.after.exists, false);

    const renamed = await compareFile({
        filename: "src/new.js",
        previousFilename: "src/old.js",
        before: "old path",
        after: "new path"
    });
    assert.equal(renamed.file.previousFilename, "src/old.js");
    assert.equal(renamed.before.text, "old path");
    assert.equal(renamed.after.text, "new path");

    const binary = await compareFile({ filename: "image.bin", before: "text", after: Buffer.from([0, 1, 2]) });
    assert.equal(binary.after.binary, true);

    const unsupported = await compareFile({ filename: "large.bin", before: "text", after: "unsupported" });
    assert.equal(unsupported.after.unsupported, true);

    const invalidUtf8 = await compareFile({
        filename: "encoded.txt",
        before: "text",
        after: Buffer.from([0xc3, 0x28])
    });
    assert.equal(invalidUtf8.after.unsupported, true);
});

test("rejects invalid PR pagination and reports GitHub API rate limits", async () => {
    await assert.rejects(
        listPullRequests("https://github.com/owner/repo", "main", { page: 1001 }),
        /page is invalid/
    );
    await assert.rejects(
        listPullRequests("https://github.com/owner/repo", "main", {
            fetchImpl: async () => jsonResponse({}, {
                status: 403,
                headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "0" }
            })
        }),
        /GitHub API rate limit reached/
    );
});

test("imports an immutable commit archive and records the selected revision", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    const archive = await createZip([
        { name: "owner-repo-a1/src/index.js", contents: "console.log('test');" },
        { name: "owner-repo-a1/README.md", contents: "Example project" }
    ]);
    const sha = "a".repeat(40);
    const requests = [];
    const result = await importRepository("https://github.com/owner/repo", "feature/demo", {
        userDataDirectory,
        id: "01234567-89ab-cdef-0123-456789abcdef",
        fetchImpl: async url => {
            requests.push(url);
            if (url.endsWith("/commits/feature%2Fdemo")) return jsonResponse({ sha });
            if (url.endsWith(`/zipball/${sha}`)) return new Response(archive);
            throw new Error(`Unexpected request: ${url}`);
        }
    });

    assert.ok(requests[0].endsWith("/commits/feature%2Fdemo"));
    assert.ok(requests[1].endsWith(`/zipball/${sha}`));
    assert.equal(result.commitSha, sha);
    assert.equal(result.fileCount, 2);
    assert.equal(fs.readFileSync(path.join(result.workspacePath, "src", "index.js"), "utf8"), "console.log('test');");
    assert.equal(JSON.parse(fs.readFileSync(path.join(path.dirname(result.workspacePath), "metadata.json"), "utf8")).branch, "feature/demo");
});

test("rejects archive path traversal and removes the incomplete import", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    const archive = await createZip([
        { name: "owner-repo-abc/safe.txt", contents: "unsafe" }
    ]);
    const safeName = Buffer.from("owner-repo-abc/safe.txt");
    const unsafeName = Buffer.from("owner-repo-abc/../a.txt");
    assert.equal(safeName.length, unsafeName.length);
    for (let offset = archive.indexOf(safeName); offset !== -1; offset = archive.indexOf(safeName, offset + safeName.length)) {
        unsafeName.copy(archive, offset);
    }
    const sha = "b".repeat(40);
    await assert.rejects(
        importRepository("https://github.com/owner/repo", "main", {
            userDataDirectory,
            id: "11234567-89ab-cdef-0123-456789abcdef",
            fetchImpl: async url => url.endsWith("/commits/main")
                ? jsonResponse({ sha })
                : new Response(archive)
        }),
        /invalid relative path|path traversal|unsafe file path/
    );
    assert.deepEqual(fs.readdirSync(path.join(userDataDirectory, "github-imports")), []);
    assert.equal(fs.existsSync(path.join(userDataDirectory, "escape.txt")), false);
});

test("rejects Windows device names in repository archives", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    const archive = await createZip([
        { name: "owner-repo-abc/CON.txt", contents: "unsafe" }
    ]);
    const sha = "c".repeat(40);
    await assert.rejects(importRepository("https://github.com/owner/repo", "main", {
        userDataDirectory,
        id: "81234567-89ab-cdef-0123-456789abcdef",
        fetchImpl: async url => url.endsWith("/commits/main")
            ? jsonResponse({ sha })
            : new Response(archive)
    }), /unsafe or unsupported file path/);
    assert.deepEqual(fs.readdirSync(path.join(userDataDirectory, "github-imports")), []);
});

test("removes its partial workspace when GitHub is unreachable", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    await assert.rejects(
        importRepository("https://github.com/owner/repo", "main", {
            userDataDirectory,
            id: "21234567-89ab-cdef-0123-456789abcdef",
            fetchImpl: async () => { throw new Error("offline"); }
        }),
        /Could not reach GitHub: offline/
    );
    assert.deepEqual(fs.readdirSync(path.join(userDataDirectory, "github-imports")), []);
});

test("preserves an existing partial workspace when an import ID collides", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    const importsDirectory = path.join(userDataDirectory, "github-imports");
    const importId = "71234567-89ab-cdef-0123-456789abcdef";
    const existingPartial = path.join(importsDirectory, `${importId}.partial`);
    fs.mkdirSync(existingPartial, { recursive: true });
    fs.writeFileSync(path.join(existingPartial, "marker.txt"), "preserve");

    await assert.rejects(importRepository("https://github.com/owner/repo", "main", {
        userDataDirectory,
        id: importId,
        fetchImpl: async () => { throw new Error("should not be called"); }
    }));
    assert.equal(fs.readFileSync(path.join(existingPartial, "marker.txt"), "utf8"), "preserve");
});

test("cancels an import and cleans its partial files", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    const controller = new AbortController();
    await assert.rejects(
        importRepository("https://github.com/owner/repo", "main", {
            userDataDirectory,
            signal: controller.signal,
            id: "31234567-89ab-cdef-0123-456789abcdef",
            fetchImpl: async () => {
                controller.abort();
                throw new Error("aborted");
            }
        }),
        /import was cancelled/
    );
    assert.deepEqual(fs.readdirSync(path.join(userDataDirectory, "github-imports")), []);
});

test("removes stale incomplete imports but preserves recent ones and completed imports", async t => {
    const userDataDirectory = createTemporaryDirectory(t);
    const importsDirectory = path.join(userDataDirectory, "github-imports");
    fs.mkdirSync(importsDirectory);
    const stale = path.join(importsDirectory, "41234567-89ab-cdef-0123-456789abcdef.partial");
    const recent = path.join(importsDirectory, "51234567-89ab-cdef-0123-456789abcdef.partial");
    const completed = path.join(importsDirectory, "61234567-89ab-cdef-0123-456789abcdef");
    for (const directory of [stale, recent, completed]) fs.mkdirSync(directory);
    const oldTime = new Date(Date.now() - 48 * 60 * 60 * 1000);
    fs.utimesSync(stale, oldTime, oldTime);

    await cleanupIncompleteImports(userDataDirectory);
    assert.equal(fs.existsSync(stale), false);
    assert.equal(fs.existsSync(recent), true);
    assert.equal(fs.existsSync(completed), true);
});
