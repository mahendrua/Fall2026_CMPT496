/*
 * upgrade.js
 *
 * The Upgrade screen and its test-mode checkout. Loaded after renderer.js
 * (uses showPage) and before auth.bundle.js (so it hears the first sign-in).
 *
 * Card handling: the dummy card number exists only in the #upgradeCardNumber
 * input. It is never logged, stored, or passed to electronAPI. Checkout calls
 * activateTestSubscription() with no arguments, and the input is cleared on
 * checkout, on leaving the screen and on sign-out.
 */
(function () {
    "use strict";

    const { createSubscriptionState, PRICE_LABEL } = window.CheckpointSubscription;
    const subscription = createSubscriptionState(window.electronAPI);
    window.checkpointSubscription = subscription;

    const DUMMY_CARD = "4242 4242 4242 4242";
    const element = id => document.getElementById(id);
    const cardInput = element("upgradeCardNumber");
    let returnPage = "homePage";
    let successMessage = "";

    function formatDate(iso) {
        const date = new Date(iso);
        return Number.isFinite(date.getTime())
            ? date.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })
            : "an unknown date";
    }

    function clearCard() {
        cardInput.value = "";
    }

    function describe(state) {
        const view = state.view;
        if (state.phase === "signed-out") return { status: "Signed out", detail: "" };
        if (state.phase === "loading" && !view) return { status: "Checking…", detail: "" };
        if (state.phase === "error" || !view) {
            return {
                status: "Free",
                detail: `${state.error || "Your subscription could not be verified."} Paid features stay locked until it can be checked.`,
                retry: true
            };
        }
        if (subscription.hasFeature("gui") && subscription.hasFeature("github")) {
            return {
                status: view.test ? "Checkpoint Pro (test subscription)" : "Checkpoint Pro",
                detail: `GUI testing and GitHub integration are unlocked until ${formatDate(view.expiresAt)}. Access does not renew automatically.`
            };
        }
        if (view.status === "expired") {
            return { status: "Free", detail: `Your Checkpoint Pro test subscription expired on ${formatDate(view.expiresAt)}.` };
        }
        if (view.status === "unverified" || view.status === "unknown" || !view.verified) {
            return {
                status: "Free",
                detail: "Your subscription could not be verified. Paid features stay locked until it can be checked.",
                retry: true
            };
        }
        return { status: "Free", detail: `Upgrade to Checkpoint Pro for ${PRICE_LABEL} to unlock GUI testing and GitHub integration.` };
    }

    function render(state) {
        const summary = describe(state);
        const active = subscription.hasFeature("gui") && subscription.hasFeature("github");
        const checkoutAvailable = state.view?.testCheckoutAvailable === true;

        // The mock checkout is shown only when the server reports test mode on.
        const showCheckout = state.phase === "ready" && !active && checkoutAvailable;
        const unavailable = state.phase === "ready" && !active && !checkoutAvailable;

        element("upgradePlanStatus").textContent = summary.status;
        element("upgradePlanDetail").textContent = unavailable
            ? `${summary.detail} Checkout is not available: test mode is turned off on the subscription server.`
            : summary.detail;
        element("upgradeRefreshBtn").classList.toggle("hidden", !summary.retry);
        element("upgradeBtnLabel").textContent = active ? "Checkpoint Pro" : "Upgrade";
        element("upgradeCheckout").classList.toggle("hidden", !showCheckout);
        if (!showCheckout) clearCard();

        const success = element("upgradeSuccessMessage");
        success.textContent = active ? successMessage : "";
        success.classList.toggle("hidden", !(active && successMessage));
        if (!active) successMessage = "";

        if (active) element("upgradeLockedMessage").classList.add("hidden");
    }

    function setCheckoutBusy(busy) {
        const confirm = element("upgradeConfirmBtn");
        confirm.disabled = busy;
        element("upgradeDummyCardBtn").disabled = busy;
        cardInput.disabled = busy;
        confirm.textContent = busy ? "Completing test purchase…" : "Confirm test purchase · $19.99";
    }

    // featureLabel, when given, explains which paid feature sent the user here.
    function openUpgradePage({ featureLabel = null, from = "homePage" } = {}) {
        returnPage = from || "homePage";
        const locked = element("upgradeLockedMessage");
        locked.textContent = featureLabel
            ? `${featureLabel} is part of Checkpoint Pro. Upgrade for ${PRICE_LABEL} to unlock it.`
            : "";
        locked.classList.toggle("hidden", !featureLabel);
        element("upgradeCheckoutError").textContent = "";
        showPage("upgradePage");
        render(subscription.getState());
        subscription.load({ force: true }).catch(() => {});
    }
    window.openUpgradePage = openUpgradePage;

    element("upgradeBtn").addEventListener("click", () => openUpgradePage({ from: "homePage" }));

    element("upgradeBackBtn").addEventListener("click", () => {
        clearCard();
        element("upgradeCheckoutError").textContent = "";
        showPage(returnPage);
    });

    element("upgradeRefreshBtn").addEventListener("click", () => {
        subscription.load({ force: true }).catch(() => {});
    });

    element("upgradeDummyCardBtn").addEventListener("click", () => {
        cardInput.value = DUMMY_CARD;
        element("upgradeCheckoutError").textContent = "";
        element("upgradeConfirmBtn").focus();
    });

    element("upgradeConfirmBtn").addEventListener("click", async () => {
        if (subscription.isActivating()) return;
        const error = element("upgradeCheckoutError");
        // Any dummy number is accepted: no card network or checksum validation.
        const hasCard = cardInput.value.trim().length > 0;
        clearCard();
        if (!hasCard) {
            error.textContent = "Enter any dummy card number, or select Use dummy card.";
            cardInput.focus();
            return;
        }

        error.textContent = "";
        setCheckoutBusy(true);
        try {
            const view = await subscription.activate();
            successMessage = view?.active
                ? `Test purchase complete. You were not charged. Checkpoint Pro is active until ${formatDate(view.expiresAt)}.`
                : "";
            if (!view?.active) error.textContent = "Checkout finished, but the subscription could not be verified. Select Check again.";
            render(subscription.getState());
        } catch (checkoutError) {
            error.textContent = checkoutError.message;
        } finally {
            setCheckoutBusy(false);
        }
    });

    // Re-check with the server when the subscription reaches its expiry time,
    // so open screens lock then rather than at the next action.
    const MAX_TIMER_MS = 2 ** 31 - 1;
    let expiryTimer = null;
    function scheduleExpiryCheck(state) {
        clearTimeout(expiryTimer);
        expiryTimer = null;
        const expiresAt = Date.parse(state.view?.expiresAt);
        if (!state.view?.active || !Number.isFinite(expiresAt)) return;
        const delay = Math.min(Math.max(expiresAt - Date.now() + 1000, 1000), MAX_TIMER_MS);
        expiryTimer = setTimeout(() => subscription.load({ force: true }).catch(() => {}), delay);
    }

    subscription.subscribe(state => {
        render(state);
        if (state.phase !== "loading") scheduleExpiryCheck(state);
        window.dispatchEvent(new CustomEvent("checkpoint-subscription-changed"));
    });

    window.addEventListener("checkpoint-authenticated", () => {
        subscription.load().catch(() => {});
    });

    window.addEventListener("checkpoint-signed-out", () => {
        clearCard();
        successMessage = "";
        element("upgradeCheckoutError").textContent = "";
        subscription.reset();
    });

    render(subscription.getState());
    window.dispatchEvent(new CustomEvent("checkpoint-subscription-changed"));
})();
