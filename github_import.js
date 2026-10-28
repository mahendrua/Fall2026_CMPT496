const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { Transform } = require("node:stream");
const { pipeline } = require("node:stream/promises");
const yauzl = require("yauzl");

const GITHUB_API = "https://api.github.com";
const MAX_ARCHIVE_BYTES = 500 * 1024 * 1024;
const MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024;
const MAX_FILE_BYTES = 200 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES = 50000;

function parseRepositoryUrl(input) {
    if (typeof input !== "string" || !input.trim()) {
        throw new Error("Enter a GitHub repository URL.");
    }

    let url;
    try {
        url = new URL(input.trim());
    } catch {
        throw new Error("Enter a valid HTTPS GitHub repository URL, such as https://github.com/owner/repository.");
    }
    if (
        url.protocol !== "https:" ||
        url.hostname.toLowerCase() !== "github.com" ||
        url.port ||
        url.username ||
        url.password ||
        url.search ||
        url.hash
    ) {
        throw new Error("Only public repositories at https://github.com/owner/repository are supported.");
    }

    const match = url.pathname.match(/^\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?\/?$/);
    if (!match || match[2] === "." || match[2] === ".." || match[2].endsWith(".")) {
        throw new Error("Enter a valid GitHub repository URL with an owner and repository name.");
    }
    return {
        owner: match[1],
        repo: match[2].replace(/\.git$/i, ""),
        url: `https://github.com/${match[1]}/${match[2].replace(/\.git$/i, "")}`
    };
}

function validateBranchName(branch) {
    if (
        typeof branch !== "string" ||
        !branch ||
        branch.length > 255 ||
        branch.startsWith("-") ||
        branch.startsWith("/") ||
        branch.endsWith("/") ||
        branch.endsWith(".") ||
        branch.includes("..") ||
        branch.includes("//") ||
        branch.includes("@{") ||
        /[\u0000-\u0020\u007f~^:?*\\[]/.test(branch) ||
        branch.split("/").some(part => !part || part.startsWith(".") || part.endsWith(".lock"))
    ) {
        throw new Error("The selected branch name is invalid.");
    }
    return branch;
}

function parseNextLink(linkHeader) {
    if (!linkHeader) return null;
    for (const part of linkHeader.split(",")) {
        const match = part.match(/<([^>]+)>;\s*rel="next"/);
        if (match) return match[1];
    }
    return null;
}

function githubError(response, repository) {
    if (response.status === 404) {
        return new Error(`Repository ${repository.owner}/${repository.repo} was not found or is private. Only public repositories can be imported.`);
    }
    if (response.status === 403 && response.headers.get("x-ratelimit-remaining") === "0") {
        const reset = Number(response.headers.get("x-ratelimit-reset"));
        const when = Number.isFinite(reset) ? ` Try again after ${new Date(reset * 1000).toLocaleTimeString()}.` : " Try again later.";
        return new Error(`GitHub API rate limit reached.${when}`);
    }
    if (response.status === 403 || response.status === 429) {
        return new Error("GitHub denied the request or rate limited this app. Wait a little while, then retry.");
    }
    return new Error(`GitHub request failed with HTTP ${response.status}.`);
}

async function githubRequest(url, repository, signal, fetchImpl) {
    let response;
    try {
        response = await fetchImpl(url, {
            signal,
            headers: {
                Accept: "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Checkpoint-Desktop"
            }
        });
    } catch (error) {
        if (signal?.aborted) throw new Error("GitHub import was cancelled.");
        throw new Error(`Could not reach GitHub: ${error.message}`);
    }
    if (!response.ok) throw githubError(response, repository);
    return response;
}

async function getRepositoryBranches(input, {
    signal,
    fetchImpl = globalThis.fetch,
    onProgress = () => {}
} = {}) {
    const repository = parseRepositoryUrl(input);
    onProgress("Fetching repository information...");
    const repositoryResponse = await githubRequest(
        `${GITHUB_API}/repos/${repository.owner}/${repository.repo}`,
        repository,
        signal,
        fetchImpl
    );
    const repositoryInfo = await repositoryResponse.json();
    if (!repositoryInfo.default_branch || repositoryInfo.private) {
        throw new Error("This repository is private or does not have a default branch. Only public repositories are supported.");
    }

    onProgress("Loading branches...");
    const branches = [];
    let pageUrl = `${GITHUB_API}/repos/${repository.owner}/${repository.repo}/branches?per_page=100&page=1`;
    const visitedPages = new Set();
    while (pageUrl) {
        if (visitedPages.has(pageUrl)) {
            throw new Error("GitHub returned a repeated branch pagination link.");
        }
        visitedPages.add(pageUrl);
        const branchResponse = await githubRequest(pageUrl, repository, signal, fetchImpl);
        const page = await branchResponse.json();
        if (!Array.isArray(page)) throw new Error("GitHub returned an invalid branch list.");
        for (const branch of page) {
            if (typeof branch?.name !== "string") throw new Error("GitHub returned an invalid branch name.");
            branches.push(branch.name);
        }
        pageUrl = parseNextLink(branchResponse.headers.get("link"));
        if (pageUrl && new URL(pageUrl).origin !== GITHUB_API) {
            throw new Error("GitHub returned an invalid branch pagination link.");
        }
    }
    if (!branches.length) throw new Error("This repository does not have any branches to import.");

    const uniqueBranches = [...new Set(branches)];
    uniqueBranches.sort((left, right) => left.localeCompare(right));
    const defaultIndex = uniqueBranches.indexOf(repositoryInfo.default_branch);
    if (defaultIndex > 0) {
        uniqueBranches.splice(defaultIndex, 1);
        uniqueBranches.unshift(repositoryInfo.default_branch);
    }
    if (defaultIndex < 0) {
        throw new Error("GitHub's default branch was not present in the branch list. Retry fetching branches.");
    }

    return {
        owner: repository.owner,
        repo: repository.repo,
        url: repository.url,
        defaultBranch: repositoryInfo.default_branch,
        branches: uniqueBranches
    };
}

function createZipFile(zipPath, options) {
    return new Promise((resolve, reject) => {
        yauzl.open(zipPath, {
            lazyEntries: true,
            autoClose: false,
            decodeStrings: true,
            validateEntrySizes: true,
            strictFileNames: true,
            ...options
        }, (error, zipFile) => error ? reject(error) : resolve(zipFile));
    });
}

function readEntry(zipFile, entry) {
    return new Promise((resolve, reject) => {
        zipFile.openReadStream(entry, (error, stream) => {
            if (error) reject(error);
            else resolve(stream);
        });
    });
}

function relativeArchiveEntry(entryName, rootName) {
    if (
        typeof entryName !== "string" ||
        entryName.includes("\\") ||
        entryName.includes("\0") ||
        entryName.startsWith("/") ||
        /^[A-Za-z]:/.test(entryName)
    ) {
        throw new Error("The GitHub archive contains an unsafe file path.");
    }
    const isDirectory = entryName.endsWith("/");
    const components = entryName.split("/");
    if (isDirectory) components.pop();
    if (!components.length || !components[0]) {
        throw new Error("The GitHub archive contains an invalid root directory.");
    }
    if (rootName && components[0] !== rootName) {
        throw new Error("The GitHub archive contains files outside its expected root directory.");
    }
    const currentRoot = components[0];
    const relativeParts = components.slice(1);
    if (relativeParts.some(part =>
        !part ||
        part === "." ||
        part === ".." ||
        /[<>:"|?*\u0000-\u001f]/.test(part) ||
        /[. ]$/.test(part) ||
        /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part)
    )) {
        throw new Error("The GitHub archive contains an unsafe or unsupported file path.");
    }
    return { currentRoot, relativePath: relativeParts.join(path.sep), isDirectory };
}

function extractArchive(zipPath, destination, { signal, onProgress = () => {} } = {}) {
    return new Promise((resolve, reject) => {
        let zipFile;
        let settled = false;
        let rootName = null;
        let processingEntry = false;
        let entryCount = 0;
        let fileCount = 0;
        let extractedBytes = 0;
        const destinations = new Set();
        const seenFiles = new Set();

        const finish = error => {
            if (settled) return;
            settled = true;
            signal?.removeEventListener("abort", onAbort);
            if (zipFile) zipFile.close();
            if (error) reject(error);
            else resolve({ entryCount, fileCount, extractedBytes });
        };
        const onAbort = () => {
            if (!processingEntry) finish(new Error("GitHub import was cancelled."));
        };
        if (signal?.aborted) return onAbort();
        signal?.addEventListener("abort", onAbort, { once: true });

        createZipFile(zipPath).then(openedZip => {
            if (settled) {
                openedZip.close();
                return;
            }
            zipFile = openedZip;

            zipFile.on("error", finish);
            zipFile.on("entry", entry => {
                processingEntry = true;
                (async () => {
                    if (signal?.aborted) throw new Error("GitHub import was cancelled.");
                    entryCount += 1;
                    if (entryCount > MAX_ARCHIVE_ENTRIES) throw new Error("The repository archive contains too many files.");
                    if (entry.generalPurposeBitFlag & 1) throw new Error("Encrypted files are not supported in a GitHub repository archive.");
                    if (entry.uncompressedSize > MAX_FILE_BYTES) throw new Error("The repository archive contains a file larger than the import limit.");
                    extractedBytes += entry.uncompressedSize;
                    if (extractedBytes > MAX_EXTRACTED_BYTES) throw new Error("The repository expands beyond the import size limit.");

                    const location = relativeArchiveEntry(entry.fileName, rootName);
                    rootName = location.currentRoot;
                    if (!location.relativePath) {
                        processingEntry = false;
                        zipFile.readEntry();
                        return;
                    }

                    const targetPath = path.resolve(destination, location.relativePath);
                    const relativePath = path.relative(destination, targetPath);
                    if (
                        !relativePath ||
                        relativePath === ".." ||
                        relativePath.startsWith(`..${path.sep}`) ||
                        path.isAbsolute(relativePath)
                    ) {
                        throw new Error("The GitHub archive contains a path outside the import workspace.");
                    }
                    const normalizedKey = relativePath.toLocaleLowerCase("en-US");
                    if (destinations.has(normalizedKey)) {
                        throw new Error("The GitHub archive contains duplicate paths that conflict on this platform.");
                    }
                    destinations.add(normalizedKey);

                    const platformMode = entry.versionMadeBy >>> 8;
                    const entryMode = entry.externalFileAttributes >>> 16;
                    const entryType = entryMode & 0o170000;
                    if (
                        platformMode === 3 &&
                        entryType !== 0 &&
                        entryType !== 0o100000 &&
                        entryType !== 0o040000
                    ) {
                        throw new Error("The GitHub archive contains an unsupported link or special file.");
                    }
                    if (entry.isSymbolicLink?.()) {
                        throw new Error("Symbolic links are not allowed in GitHub repository archives.");
                    }

                    if (location.isDirectory) {
                        if (seenFiles.has(normalizedKey)) throw new Error("The GitHub archive contains conflicting file paths.");
                        await fsp.mkdir(targetPath, { recursive: true });
                    } else {
                        if (entryType === 0o040000) throw new Error("The GitHub archive contains a malformed directory entry.");
                        if (seenFiles.has(normalizedKey)) throw new Error("The GitHub archive contains duplicate file paths.");
                        seenFiles.add(normalizedKey);
                        fileCount += 1;
                        await fsp.mkdir(path.dirname(targetPath), { recursive: true });
                        const input = await readEntry(zipFile, entry);
                        let writtenBytes = 0;
                        const limiter = new Transform({
                            transform(chunk, _encoding, callback) {
                                writtenBytes += chunk.length;
                                if (writtenBytes > entry.uncompressedSize || writtenBytes > MAX_FILE_BYTES) {
                                    callback(new Error("The GitHub archive entry exceeded its declared size."));
                                    return;
                                }
                                callback(null, chunk);
                            }
                        });
                        await pipeline(input, limiter, fs.createWriteStream(targetPath, { flags: "wx" }), { signal });
                        if (writtenBytes !== entry.uncompressedSize) {
                            throw new Error("The GitHub archive entry size did not match its header.");
                        }
                    }
                    if (entryCount % 250 === 0) onProgress(`Preparing project files (${entryCount} entries)...`);
                    processingEntry = false;
                    zipFile.readEntry();
                })().catch(error => {
                    processingEntry = false;
                    finish(error);
                });
            });
            zipFile.on("end", () => {
                if (!entryCount || !rootName) {
                    finish(new Error("The GitHub archive did not contain a project."));
                    return;
                }
                if (!fileCount) {
                    finish(new Error("The selected repository revision contains no project files."));
                    return;
                }
                finish();
            });
            zipFile.readEntry();
        }).catch(finish);
    });
}

async function downloadArchive(url, zipPath, repository, { signal, fetchImpl, onProgress }) {
    const response = await githubRequest(url, repository, signal, fetchImpl);
    const contentLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(contentLength) && contentLength > MAX_ARCHIVE_BYTES) {
        throw new Error("The repository archive is larger than the 500 MB download limit.");
    }
    if (!response.body) throw new Error("GitHub returned an empty repository archive.");

    let downloadedBytes = 0;
    const limiter = new Transform({
        transform(chunk, _encoding, callback) {
            downloadedBytes += chunk.length;
            if (downloadedBytes > MAX_ARCHIVE_BYTES) {
                callback(new Error("The repository archive is larger than the 500 MB download limit."));
                return;
            }
            if (contentLength > 0) {
                onProgress(`Downloading repository (${Math.min(99, Math.floor(downloadedBytes / contentLength * 100))}%)...`);
            }
            callback(null, chunk);
        }
    });
    await pipeline(
        require("node:stream").Readable.fromWeb(response.body),
        limiter,
        fs.createWriteStream(zipPath, { flags: "wx" }),
        { signal }
    );
    if (downloadedBytes === 0) throw new Error("GitHub returned an empty repository archive.");
}

async function importRepository(input, branch, {
    userDataDirectory,
    signal,
    fetchImpl = globalThis.fetch,
    onProgress = () => {},
    id = randomUUID()
} = {}) {
    const repository = parseRepositoryUrl(input);
    validateBranchName(branch);
    if (!userDataDirectory) throw new Error("The Checkpoint import directory is unavailable.");
    if (typeof id !== "string" || !/^[0-9a-f-]{36}$/i.test(id)) {
        throw new Error("The import identifier is invalid.");
    }

    const importsDirectory = path.join(userDataDirectory, "github-imports");
    await fsp.mkdir(importsDirectory, { recursive: true });
    const partialDirectory = path.join(importsDirectory, `${id}.partial`);
    const finalDirectory = path.join(importsDirectory, id);
    const archivePath = path.join(partialDirectory, "repository.zip");
    const workspacePath = path.join(partialDirectory, "workspace");
    let partialCreated = false;

    try {
        await fsp.mkdir(partialDirectory, { recursive: false });
        partialCreated = true;
        await fsp.mkdir(workspacePath, { recursive: false });
        onProgress("Resolving selected commit...");
        const commitResponse = await githubRequest(
            `${GITHUB_API}/repos/${repository.owner}/${repository.repo}/commits/${encodeURIComponent(branch)}`,
            repository,
            signal,
            fetchImpl
        );
        const commit = await commitResponse.json();
        if (typeof commit?.sha !== "string" || !/^[0-9a-f]{40}$/i.test(commit.sha)) {
            throw new Error("GitHub did not return a valid commit SHA for the selected branch.");
        }

        onProgress("Downloading repository...");
        await downloadArchive(
            `${GITHUB_API}/repos/${repository.owner}/${repository.repo}/zipball/${commit.sha}`,
            archivePath,
            repository,
            { signal, fetchImpl, onProgress }
        );

        onProgress("Preparing project...");
        const extracted = await extractArchive(archivePath, workspacePath, { signal, onProgress });
        await fsp.rm(archivePath, { force: true });
        const metadata = {
            id,
            owner: repository.owner,
            repository: repository.repo,
            repositoryUrl: repository.url,
            branch,
            commitSha: commit.sha,
            workspacePath: path.join(finalDirectory, "workspace"),
            importedAt: new Date().toISOString(),
            fileCount: extracted.fileCount
        };
        await fsp.writeFile(path.join(partialDirectory, "metadata.json"), JSON.stringify(metadata, null, 2), {
            encoding: "utf8",
            flag: "wx",
            mode: 0o600
        });
        await fsp.rename(partialDirectory, finalDirectory);
        onProgress("Project ready!");
        return metadata;
    } catch (error) {
        if (partialCreated) {
            await fsp.rm(partialDirectory, { recursive: true, force: true }).catch(cleanupError => {
                console.error("Could not remove incomplete GitHub import:", cleanupError);
            });
        }
        if (signal?.aborted || error.name === "AbortError") {
            throw new Error("GitHub import was cancelled.");
        }
        throw error;
    }
}

async function cleanupIncompleteImports(userDataDirectory, olderThanMs = 24 * 60 * 60 * 1000) {
    const importsDirectory = path.join(userDataDirectory, "github-imports");
    let entries;
    try {
        entries = await fsp.readdir(importsDirectory, { withFileTypes: true });
    } catch (error) {
        if (error.code === "ENOENT") return;
        throw error;
    }

    const cutoff = Date.now() - olderThanMs;
    for (const entry of entries) {
        if (!entry.isDirectory() || !/^[0-9a-f-]{36}\.partial$/i.test(entry.name)) continue;
        const entryPath = path.join(importsDirectory, entry.name);
        const stats = await fsp.stat(entryPath);
        if (stats.mtimeMs < cutoff) await fsp.rm(entryPath, { recursive: true, force: true });
    }
}

module.exports = {
    cleanupIncompleteImports,
    getRepositoryBranches,
    importRepository,
    parseRepositoryUrl,
    validateBranchName
};
