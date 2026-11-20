/*
 * subscription_state.js
 *
 * The renderer's shared view of the signed-in account's Checkpoint Pro
 * entitlement, without the DOM. Every screen asks hasFeature() instead of
 * keeping its own flag. This only decides what the UI shows: the main
 * process enforces access on every paid operation regardless.
 *
 * Loaded by index.html before renderer.js, and by node --test.
 */
(function (root, factory) {
    const api = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.CheckpointSubscription = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    const PRICE_LABEL = "$19.99/month";
    const FEATURES = Object.freeze({
        gui: "GUI testing",
        github: "GitHub integration"
    });
    const PAYMENT_REQUIRED_PREFIX = "Checkpoint Pro required:";

    function isPaymentRequiredError(error) {
        const message = typeof error === "string" ? error : error?.message;
        return typeof message === "string" && message.includes(PAYMENT_REQUIRED_PREFIX);
    }

    // Electron prefixes errors thrown in the main process with
    // "Error invoking remote method '...': Error: ". Show only the message.
    function cleanErrorMessage(error, fallback) {
        const message = typeof error === "string" ? error : error?.message;
        if (typeof message !== "string" || !message.trim()) return fallback;
        return message.replace(/^Error invoking remote method '[^']*':\s*(\w*Error:\s*)?/, "").trim() || fallback;
    }

    function createSubscriptionState(bridge, { now = Date.now } = {}) {
        let state = { phase: "signed-out", view: null, error: null };
        let generation = 0;
        let activation = null;
        const listeners = new Set();

        function setState(next) {
            state = { ...state, ...next };
            listeners.forEach(listener => {
                try {
                    listener(state);
                } catch (error) {
                    console.error("Subscription listener failed:", error);
                }
            });
        }

        function hasFeature(feature) {
            const view = state.view;
            return Boolean(
                Object.prototype.hasOwnProperty.call(FEATURES, feature) &&
                view?.active === true &&
                view.features?.[feature] === true &&
                Date.parse(view.expiresAt) > now()
            );
        }

        async function load({ force = false } = {}) {
            const current = ++generation;
            setState({ phase: "loading", error: null });
            try {
                const view = force ? await bridge.refreshSubscription() : await bridge.getSubscription();
                if (current === generation) setState({ phase: "ready", view, error: null });
            } catch (error) {
                if (current === generation) {
                    setState({
                        phase: "error",
                        view: null,
                        error: cleanErrorMessage(error, "Your subscription could not be verified.")
                    });
                }
            }
            return state;
        }

        // One activation at a time; extra clicks join the one in flight.
        // The bridge call takes no arguments, so no card data can be sent.
        function activate() {
            if (activation) return activation;
            const current = generation;
            activation = (async () => {
                try {
                    const view = await bridge.activateTestSubscription();
                    if (current === generation) setState({ phase: "ready", view, error: null });
                    return view;
                } catch (error) {
                    throw new Error(cleanErrorMessage(error, "The test checkout could not be completed."));
                } finally {
                    activation = null;
                }
            })();
            return activation;
        }

        // Sign-out or account switch: forget everything about the last account.
        function reset() {
            generation += 1;
            activation = null;
            setState({ phase: "signed-out", view: null, error: null });
        }

        function subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        }

        return {
            activate,
            getState: () => state,
            hasFeature,
            isActivating: () => activation !== null,
            load,
            reset,
            subscribe
        };
    }

    return {
        FEATURES,
        PAYMENT_REQUIRED_PREFIX,
        PRICE_LABEL,
        cleanErrorMessage,
        createSubscriptionState,
        isPaymentRequiredError
    };
});
