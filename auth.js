import { initializeApp } from "@firebase/app";
import {
    createUserWithEmailAndPassword,
    browserLocalPersistence,
    initializeAuth,
    inMemoryPersistence,
    onIdTokenChanged,
    sendPasswordResetEmail,
    setPersistence,
    signInWithEmailAndPassword,
    signOut,
    updateProfile
} from "@firebase/auth";

const api = window.electronAPI;
const views = {
    login: document.getElementById("loginFormView"),
    registration: document.getElementById("registrationFormView")
};
let auth;
let sessionEstablished = false;
let authTransition = false;
let initialAuthStateResolved = false;
const persistenceListeners = new Map();

class ElectronSecurePersistence extends browserLocalPersistence {
    async _isAvailable() {
        return true;
    }

    async _set(key, value) {
        await api.writeAuthSession(value);
        persistenceListeners.get(key)?.forEach(listener => listener(value));
    }

    async _get() {
        return api.readAuthSession();
    }

    async _remove(key) {
        await api.clearAuthSession();
        persistenceListeners.get(key)?.forEach(listener => listener(null));
    }

    _addListener(key, listener) {
        if (!persistenceListeners.has(key)) persistenceListeners.set(key, new Set());
        persistenceListeners.get(key).add(listener);
    }

    _removeListener(key, listener) {
        persistenceListeners.get(key)?.delete(listener);
    }
}

function showView(name) {
    Object.entries(views).forEach(([viewName, element]) => {
        element.classList.toggle("hidden", viewName !== name);
    });
    document.getElementById(name === "login" ? "loginEmail" : "registrationName").focus();
}

function setLoading(button, loading, label) {
    button.disabled = loading;
    button.innerHTML = loading
        ? `<span class="auth-spinner" aria-hidden="true"></span><span>${label}</span>`
        : `<span>${label}</span>`;
}

function setError(id, message = "") {
    document.getElementById(id).textContent = message;
}

function getAuthError(error) {
    const messages = {
        "auth/invalid-email": "Enter a valid email address.",
        "auth/invalid-credential": "The email address or password is incorrect.",
        "auth/user-not-found": "The email address or password is incorrect.",
        "auth/wrong-password": "The email address or password is incorrect.",
        "auth/email-already-in-use": "An account already exists for this email address. Try signing in instead.",
        "auth/weak-password": "Choose a stronger password that meets your Firebase project's password policy.",
        "auth/password-does-not-meet-requirements": "Choose a password that meets your Firebase project's password policy.",
        "auth/too-many-requests": "Too many attempts. Wait a little while, then try again.",
        "auth/network-request-failed": "Could not reach Firebase. Check your internet connection and try again.",
        "auth/operation-not-allowed": "Email and password sign-in is not enabled for this Firebase project.",
        "auth/invalid-api-key": "The Firebase API key is invalid. Check the Firebase configuration file."
    };
    return messages[error?.code] || error?.message || "Authentication failed. Please try again.";
}

function presentSetupInstructions(configPath) {
    const notice = document.getElementById("authSetupNotice");
    const content = document.getElementById("authFormContent");
    notice.classList.remove("hidden");
    content.classList.add("hidden");
    notice.replaceChildren();

    const heading = document.createElement("strong");
    heading.textContent = "Firebase setup required";
    const instructions = document.createElement("p");
    instructions.textContent =
        `In Firebase Console, create a Web app, enable Authentication > Sign-in method > Email/Password, `
        + `then create ${configPath} with its apiKey, authDomain, projectId, and appId values. Restart Checkpoint after saving the file.`;
    notice.append(heading, instructions);
}

function renderAuthenticatedUser(user) {
    const name = user.displayName?.trim();
    const homeIntro = document.querySelector(".home-intro");
    if (homeIntro) homeIntro.textContent = name ? `Welcome, ${name}` : "Choose where you want to go";
    document.getElementById("authScreen").classList.add("hidden");
    window.dispatchEvent(new Event("checkpoint-authenticated"));
}

async function establishUserSession(user, render = true) {
    const shouldRender = render && !sessionEstablished;
    const token = await user.getIdToken();
    await api.establishAuthSession(token);
    sessionEstablished = true;
    if (shouldRender) renderAuthenticatedUser(user);
}

async function clearMainSession() {
    sessionEstablished = false;
    await api.signOutAuthSession();
}

document.getElementById("showRegistrationBtn").addEventListener("click", () => {
    setError("loginError");
    showView("registration");
});

document.getElementById("showLoginBtn").addEventListener("click", () => {
    setError("registrationError");
    showView("login");
});

document.querySelectorAll("[data-password-toggle]").forEach(button => {
    button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.passwordToggle);
        const revealing = input.type === "password";
        input.type = revealing ? "text" : "password";
        button.textContent = revealing ? "Hide" : "Show";
        button.setAttribute("aria-label", `${revealing ? "Hide" : "Show"} password`);
    });
});

document.getElementById("rememberMe").addEventListener("change", async event => {
    if (!auth) return;
    try {
        await setPersistence(auth, event.currentTarget.checked ? electronPersistence : inMemoryPersistence);
    } catch (error) {
        event.currentTarget.checked = true;
        setError("loginError", `Could not update secure session persistence. ${getAuthError(error)}`);
    }
});

document.getElementById("loginForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (!auth || authTransition) return;

    const email = document.getElementById("loginEmail").value.trim();
    const password = document.getElementById("loginPassword").value;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        setError("loginError", "Enter a valid email address.");
        return;
    }
    if (!password) {
        setError("loginError", "Enter your password.");
        return;
    }

    const button = document.getElementById("signInBtn");
    authTransition = true;
    setError("loginError");
    setLoading(button, true, "Signing in...");
    try {
        const result = await signInWithEmailAndPassword(auth, email, password);
        await establishUserSession(result.user);
    } catch (error) {
        if (auth.currentUser) {
            await signOut(auth).catch(() => {});
            await clearMainSession().catch(() => {});
        }
        setError("loginError", getAuthError(error));
    } finally {
        authTransition = false;
        setLoading(button, false, "Sign In");
    }
});

document.getElementById("registrationForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (!auth || authTransition) return;

    const fullName = document.getElementById("registrationName").value.trim();
    const email = document.getElementById("registrationEmail").value.trim();
    const password = document.getElementById("registrationPassword").value;
    const confirmPassword = document.getElementById("registrationConfirmPassword").value;
    const professionalRole = document.getElementById("professionalRole").value;
    if (!fullName) return setError("registrationError", "Enter your full name.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return setError("registrationError", "Enter a valid email address.");
    }
    if (!password) return setError("registrationError", "Enter a password.");
    if (password.length < 6) return setError("registrationError", "Firebase requires a password with at least 6 characters.");
    if (password !== confirmPassword) return setError("registrationError", "The passwords do not match.");
    if (!professionalRole) return setError("registrationError", "Select your professional role.");

    const button = document.getElementById("createAccountBtn");
    authTransition = true;
    setError("registrationError");
    setLoading(button, true, "Creating account...");
    try {
        const result = await createUserWithEmailAndPassword(auth, email, password);
        await updateProfile(result.user, { displayName: fullName });
        await result.user.getIdToken(true);
        await establishUserSession(result.user, false);
        await api.saveUserProfile(professionalRole);
        setError("registrationError");
        renderAuthenticatedUser(result.user);
        const toast = document.getElementById("registrationSuccessToast");
        toast.classList.remove("hidden");
        window.setTimeout(() => toast.classList.add("hidden"), 5000);
        window.dispatchEvent(new CustomEvent("checkpoint-registration-success"));
    } catch (error) {
        if (auth.currentUser) {
            await signOut(auth).catch(() => {});
            await clearMainSession().catch(() => {});
        }
        setError("registrationError", getAuthError(error));
    } finally {
        authTransition = false;
        setLoading(button, false, "Create Account");
    }
});

document.getElementById("forgotPasswordBtn").addEventListener("click", async () => {
    if (!auth || authTransition) return;
    const email = document.getElementById("loginEmail").value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        setError("loginError", "Enter your email address above, then select Forgot Password?.");
        return;
    }
    const button = document.getElementById("forgotPasswordBtn");
    button.disabled = true;
    setError("loginError");
    try {
        await sendPasswordResetEmail(auth, email);
        setError("loginError", "If an account exists for this email, Firebase will send a password reset link.");
    } catch (error) {
        setError(
            "loginError",
            error?.code === "auth/user-not-found"
                ? "If an account exists for this email, Firebase will send a password reset link."
                : getAuthError(error)
        );
    } finally {
        button.disabled = false;
    }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
    if (!auth || authTransition) return;
    authTransition = true;
    document.getElementById("authScreen").classList.remove("hidden");
    try {
        await signOut(auth);
        await clearMainSession();
        showView("login");
    } catch (error) {
        setError("loginError", `Could not complete sign out. ${getAuthError(error)}`);
    } finally {
        authTransition = false;
    }
});

async function initializeAuthentication() {
    const setup = await api.getFirebaseConfig();
    if (!setup.configured) {
        presentSetupInstructions(setup.configPath);
        return;
    }

    await api.readAuthSession();
    const app = initializeApp(setup.config);
    auth = initializeAuth(app, { persistence: [ElectronSecurePersistence] });
    await setPersistence(
        auth,
        document.getElementById("rememberMe").checked
            ? ElectronSecurePersistence
            : inMemoryPersistence
    );

    onIdTokenChanged(auth, async user => {
        if (authTransition) return;
        if (user) {
            try {
                await establishUserSession(user);
            } catch (error) {
                await signOut(auth).catch(() => {});
                await clearMainSession().catch(() => {});
                setError("loginError", getAuthError(error));
                showView("login");
            }
        } else {
            if (sessionEstablished) await clearMainSession().catch(error =>
                setError("loginError", getAuthError(error))
            );
            document.getElementById("authScreen").classList.remove("hidden");
            if (initialAuthStateResolved) showView("login");
        }
        initialAuthStateResolved = true;
    });
}

initializeAuthentication().catch(error => {
    const notice = document.getElementById("authSetupNotice");
    notice.classList.remove("hidden");
    document.getElementById("authFormContent").classList.add("hidden");
    notice.textContent = `Checkpoint authentication could not start: ${getAuthError(error)}`;
});
