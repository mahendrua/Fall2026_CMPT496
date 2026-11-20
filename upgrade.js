/*
 * upgrade.js
 *
 * The Upgrade screen and its test-mode checkout dialog. Loaded after
 * renderer.js (uses showPage) and before auth.bundle.js (so it hears the
 * first sign-in).
 *
 * Flow: "Pay $19.99/month" (always clickable for a signed-in free account)
 * opens #upgradeCheckoutDialog. If the server reports test mode on, the dialog
 * shows the dummy card input; "Confirm test payment" is enabled once anything
 * is typed or "Use dummy card" is selected. If test mode is off or cannot be
 * confirmed, the dialog explains why and how to enable it locally. The server
 * still rejects any payment while test mode is off.
 *
 * Card handling: the dummy card number exists only in the dialog's
 * #upgradeCardNumber input. It is never logged, stored, or passed to
 * electronAPI: checkout calls activateTestSubscription() with no arguments.
 * The input is cleared when the dialog closes, on success and on sign-out.
 */
(function () {
    "use strict";

    const { createSubscriptionState, PRICE_LABEL } = window.CheckpointSubscription;
    const subscription = createSubscriptionState(window.electronAPI);
    window.checkpointSubscription = subscription;

    const DUMMY_CARD = "4242 4242 4242 4242";
    const element = id => document.getElementById(id);
    const dialog = element("upgradeCheckoutDialog");
    const cardInput = element("upgradeCardNumber");
    const confirmButton = element("upgradeConfirmBtn");
    let returnPage = "homePage";
    let successMessage = "";

    function formatDate(iso) {
        const date = new Date(iso);
        return Number.isFinite(date.getTime())
            ? date.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })
            : "an unknown date";
    }

    // The only place the card value is read, and only to see if anything was typed.
    // Any dummy number is accepted: no card network or checksum validation.
    function hasCardInput() {
        return cardInput.value.trim().length > 0;
    }

    function clearCard() {
        cardInput.value = "";
    }

    function isPro() {
        return subscription.hasFeature("gui") && subscription.hasFeature("github");
    }

    // "available": server confirmed test mode on. "disabled": server confirmed it
    // off. "unreachable": the server could not be asked. "checking": asking now.
    function checkoutAvailability(state) {
        if (subscription.isActivating()) return "available";
        if (state.phase === "loading") return "checking";
        if (state.phase !== "ready" || !state.view?.verified) return "unreachable";
        return state.view.testCheckoutAvailable === true ? "available" : "disabled";
    }

    function describe(state) {
        const view = state.view;
        if (state.phase === "signed-out") return { status: "Signed out", detail: "" };
        if (state.phase === "loading" && !view) return { status: "Checking…", detail: "" };
        if (isPro()) {
            return {
                status: view.test ? "Checkpoint Pro (test subscription)" : "Checkpoint Pro",
                detail: `GUI testing and GitHub integration are unlocked until ${formatDate(view.expiresAt)}. Access does not renew automatically.`
            };
        }
        if (view?.verified && view.status === "expired") {
            return { status: "Free", detail: `Your Checkpoint Pro test subscription expired on ${formatDate(view.expiresAt)}.` };
        }
        if (state.phase === "error" || !view?.verified) {
            return {
                status: "Free",
                detail: "Checkpoint couldn't reach the subscription service, so paid features stay locked until your subscription can be checked.",
                retry: true
            };
        }
        return { status: "Free", detail: `Upgrade to Checkpoint Pro for ${PRICE_LABEL} to unlock GUI testing and GitHub integration.` };
    }

    function updateConfirmButton() {
        confirmButton.disabled = subscription.isActivating() || !hasCardInput();
    }

    function renderDialog(state) {
        if (!dialog.open) return;
        if (isPro() && !subscription.isActivating()) {
            dialog.close();
            return;
        }
        const availability = checkoutAvailability(state);
        element("upgradeCheckoutChecking").classList.toggle("hidden", availability !== "checking");
        element("upgradeCheckout").classList.toggle("hidden", availability !== "available");
        element("upgradeCheckoutUnavailable").classList.toggle("hidden", availability !== "disabled" && availability !== "unreachable");
        element("upgradeUnavailableReason").textContent = availability === "disabled"
            ? "Test checkout is turned off on the subscription server, so no payment can be made. The server rejects test payments while it is off."
            : "Checkpoint couldn't reach the subscription service to confirm that test checkout is on, so no payment can be made right now.";
        confirmButton.classList.toggle("hidden", availability !== "available");
        if (availability !== "available") clearCard();
        updateConfirmButton();
    }

    function render(state) {
        const summary = describe(state);
        const active = isPro();

        element("upgradePlanStatus").textContent = summary.status;
        element("upgradePlanDetail").textContent = summary.detail;
        element("upgradeRefreshBtn").classList.toggle("hidden", !summary.retry);
        element("upgradeBtnLabel").textContent = active ? "Checkpoint Pro" : "Upgrade";
        element("upgradePayBtn").classList.toggle("hidden", active || state.phase === "signed-out");

        const success = element("upgradeSuccessMessage");
        success.textContent = active ? successMessage : "";
        success.classList.toggle("hidden", !(active && successMessage));
        if (!active) successMessage = "";
        if (active) element("upgradeLockedMessage").classList.add("hidden");

        renderDialog(state);
    }

    function setCheckoutBusy(busy) {
        confirmButton.textContent = busy ? "Activating…" : "Confirm test payment";
        element("upgradeDummyCardBtn").disabled = busy;
        element("upgradeCancelBtn").disabled = busy;
        cardInput.disabled = busy;
        updateConfirmButton();
    }

    function openCheckoutDialog() {
        element("upgradeCheckoutError").textContent = "";
        clearCard();
        if (!dialog.open) dialog.showModal();
        const state = subscription.getState();
        renderDialog(state);
        // Ask the server again unless it has just confirmed test mode is on.
        if (checkoutAvailability(state) !== "available") subscription.load({ force: true }).catch(() => {});
        else cardInput.focus();
    }

    // featureLabel, when given, explains which paid feature sent the user here.
    function openUpgradePage({ featureLabel = null, from = "homePage" } = {}) {
        returnPage = from || "homePage";
        const locked = element("upgradeLockedMessage");
        locked.textContent = featureLabel
            ? `${featureLabel} is part of Checkpoint Pro. Upgrade for ${PRICE_LABEL} to unlock it.`
            : "";
        locked.classList.toggle("hidden", !featureLabel);
        showPage("upgradePage");
        render(subscription.getState());
        subscription.load({ force: true }).catch(() => {});
    }
    window.openUpgradePage = openUpgradePage;

    element("upgradeBtn").addEventListener("click", () => openUpgradePage({ from: "homePage" }));
    element("upgradePayBtn").addEventListener("click", openCheckoutDialog);

    element("upgradeBackBtn").addEventListener("click", () => {
        if (dialog.open) dialog.close();
        showPage(returnPage);
    });

    element("upgradeRefreshBtn").addEventListener("click", () => {
        subscription.load({ force: true }).catch(() => {});
    });
    element("upgradeRecheckBtn").addEventListener("click", () => {
        element("upgradeCheckoutError").textContent = "";
        subscription.load({ force: true }).catch(() => {});
    });

    element("upgradeCancelBtn").addEventListener("click", () => dialog.close());
    // Esc closes the dialog, except while a payment is being activated.
    dialog.addEventListener("cancel", event => {
        if (subscription.isActivating()) event.preventDefault();
    });
    dialog.addEventListener("close", () => {
        clearCard();
        element("upgradeCheckoutError").textContent = "";
        updateConfirmButton();
    });

    cardInput.addEventListener("input", () => {
        element("upgradeCheckoutError").textContent = "";
        updateConfirmButton();
    });

    element("upgradeDummyCardBtn").addEventListener("click", () => {
        cardInput.value = DUMMY_CARD;
        element("upgradeCheckoutError").textContent = "";
        updateConfirmButton();
        confirmButton.focus();
    });

    confirmButton.addEventListener("click", async () => {
        if (subscription.isActivating()) return;
        const error = element("upgradeCheckoutError");
        if (!hasCardInput()) {
            error.textContent = "Enter any dummy card number, or select Use dummy card.";
            cardInput.focus();
            return;
        }

        error.textContent = "";
        const activation = subscription.activate();
        setCheckoutBusy(true);
        try {
            const view = await activation;
            if (view?.active) {
                successMessage = `Test payment complete. You were not charged. Checkpoint Pro is active until ${formatDate(view.expiresAt)}.`;
                setCheckoutBusy(false);
                dialog.close();
                render(subscription.getState());
                return;
            }
            error.textContent = "The payment was accepted, but your subscription could not be verified yet. Select Confirm test payment to try again.";
        } catch (checkoutError) {
            // The card stays in the dialog's input so Confirm can be retried.
            error.textContent = `${checkoutError.message} Select Confirm test payment to try again.`;
            if (/disabled/i.test(checkoutError.message)) subscription.load({ force: true }).catch(() => {});
        }
        setCheckoutBusy(false);
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
        if (dialog.open) dialog.close();
        clearCard();
        successMessage = "";
        subscription.reset();
    });

    render(subscription.getState());
    window.dispatchEvent(new CustomEvent("checkpoint-subscription-changed"));
})();
