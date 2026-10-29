const fs = require("node:fs");
const path = require("node:path");

const PROFESSIONAL_ROLES = [
    "Software Developer",
    "Software Tester / QA Engineer",
    "Project Manager",
    "Software Engineer",
    "Team Lead",
    "Student",
    "Other"
];

function getFirebaseConfig(userDataDirectory, bundledConfigPath = path.join(__dirname, "firebase-config.json")) {
    const userConfigPath = path.join(userDataDirectory, "firebase-config.json");
    const configPath = fs.existsSync(userConfigPath) ? userConfigPath : bundledConfigPath;
    if (!fs.existsSync(configPath)) {
        return { configured: false, configPath: userConfigPath };
    }

    let config;
    try {
        config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    } catch (error) {
        throw new Error(`Firebase configuration is not valid JSON: ${error.message}`);
    }

    const requiredFields = ["apiKey", "authDomain", "projectId", "appId"];
    if (requiredFields.some(field => typeof config[field] !== "string" || !config[field].trim())) {
        throw new Error(`Firebase configuration must include: ${requiredFields.join(", ")}.`);
    }

    return {
        configured: true,
        configPath,
        config: Object.fromEntries(
            [...requiredFields, "storageBucket", "messagingSenderId", "measurementId"]
                .filter(field => typeof config[field] === "string" && config[field].trim())
                .map(field => [field, config[field].trim()])
        )
    };
}

async function verifyFirebaseIdToken(idToken, apiKey) {
    if (typeof idToken !== "string" || idToken.length < 20 || idToken.length > 16384) {
        throw new Error("Authentication required. Please sign in again.");
    }

    const response = await fetch(
        `https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${encodeURIComponent(apiKey)}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ idToken })
        }
    );
    if (!response.ok) {
        throw new Error("Your Checkpoint session could not be verified. Please sign in again.");
    }

    const result = await response.json();
    const user = result.users?.[0];
    if (!user?.localId || !user.email) {
        throw new Error("Your Checkpoint session could not be verified. Please sign in again.");
    }

    const payload = idToken.split(".")[1];
    let claims;
    try {
        claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    } catch {
        throw new Error("Your Checkpoint session is invalid. Please sign in again.");
    }
    if (!Number.isFinite(claims.exp) || claims.exp * 1000 <= Date.now()) {
        throw new Error("Your Checkpoint session has expired. Please sign in again.");
    }

    return {
        uid: user.localId,
        email: user.email,
        displayName: user.displayName || "",
        creationTime: user.createdAt ? new Date(Number(user.createdAt)).toISOString() : null,
        expiresAt: claims.exp * 1000
    };
}

function saveLocalProfile(userDataDirectory, profile) {
    if (!profile || typeof profile.uid !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(profile.uid)) {
        throw new Error("The authenticated user profile is invalid.");
    }
    if (typeof profile.fullName !== "string" || !profile.fullName.trim() || profile.fullName.length > 200) {
        throw new Error("Enter a valid full name.");
    }
    if (typeof profile.email !== "string" || profile.email.length > 320) {
        throw new Error("The authenticated email address is invalid.");
    }
    if (!PROFESSIONAL_ROLES.includes(profile.professionalRole)) {
        throw new Error("Select a valid professional role.");
    }

    const directory = path.join(userDataDirectory, "profiles");
    fs.mkdirSync(directory, { recursive: true });
    const filePath = path.join(directory, `${profile.uid}.json`);
    const record = {
        uid: profile.uid,
        fullName: profile.fullName.trim(),
        email: profile.email,
        professionalRole: profile.professionalRole,
        accountCreatedAt: profile.accountCreatedAt || null
    };
    const temporaryPath = `${filePath}.tmp`;
    fs.writeFileSync(temporaryPath, JSON.stringify(record, null, 2), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(temporaryPath, filePath);
    return record;
}

function readLocalProfile(userDataDirectory, uid) {
    if (typeof uid !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(uid)) {
        throw new Error("The authenticated user profile is invalid.");
    }

    const filePath = path.join(userDataDirectory, "profiles", `${uid}.json`);
    if (!fs.existsSync(filePath)) return null;
    const profile = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (profile.uid !== uid || !PROFESSIONAL_ROLES.includes(profile.professionalRole)) {
        throw new Error("The local user profile is invalid.");
    }
    return profile;
}

module.exports = {
    PROFESSIONAL_ROLES,
    getFirebaseConfig,
    readLocalProfile,
    saveLocalProfile,
    verifyFirebaseIdToken
};
