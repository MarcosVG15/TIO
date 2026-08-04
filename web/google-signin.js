/*
 * Google Sign-In shim.
 *
 * The deployed React bundle has no Google integration - its "Sign in with
 * Google" button calls the API with an undefined idToken. Rather than rebuild
 * the frontend, this replaces that button with a real Google Identity
 * Services button and completes the flow itself.
 *
 * It writes the session token to the same localStorage key the app reads
 * ("tio.access_token"), so after the redirect the app hydrates normally and
 * has no idea this ran.
 *
 * STOPGAP: index.html is build output. The next frontend deploy overwrites it
 * and drops the <script> tag below. Either re-add it, or implement Google
 * properly in the frontend source and delete this file.
 */
(function () {
  "use strict";

  var CLIENT_ID =
    "688537482332-9ad5p252gu6a7qhnvoe6o47qhl7dorkt.apps.googleusercontent.com";
  var TOKEN_KEY = "tio.access_token"; // must match the app's key
  var AFTER_SIGN_IN = "/";

  function report(message) {
    console.error("[tio-google]", message);
    window.alert(message);
  }

  function onCredential(response) {
    if (!response || !response.credential) {
      report("Google returned no credential.");
      return;
    }

    fetch("/api/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ id_token: response.credential }),
    })
      .then(function (res) {
        return res.json().then(
          function (body) {
            return { ok: res.ok, status: res.status, body: body };
          },
          function () {
            return { ok: res.ok, status: res.status, body: null };
          }
        );
      })
      .then(function (result) {
        if (!result.ok) {
          var detail = (result.body && result.body.detail) || result.status;
          report("Google sign-in rejected: " + detail);
          return;
        }
        if (!result.body || !result.body.access_token) {
          report("No access_token in the response.");
          return;
        }
        // Same key the app reads, so a plain reload restores the session.
        window.localStorage.setItem(TOKEN_KEY, result.body.access_token);
        window.location.replace(AFTER_SIGN_IN);
      })
      .catch(function (err) {
        report("Could not reach the server: " + err.message);
      });
  }

  function looksLikeGoogleButton(el) {
    var text = (el.textContent || "").toLowerCase();
    return text.indexOf("google") !== -1 && text.length < 60;
  }

  function mount() {
    var candidates = document.querySelectorAll(
      'button:not([data-tio-gsi]), [role="button"]:not([data-tio-gsi])'
    );

    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (!looksLikeGoogleButton(el)) continue;

      el.setAttribute("data-tio-gsi", "1");

      // Measure before hiding - offsetWidth is 0 on a hidden element.
      var width = Math.max(240, Math.min(el.offsetWidth || 320, 400));

      var holder = document.createElement("div");
      holder.setAttribute("data-tio-gsi-holder", "1");
      holder.style.cssText = "display:flex;justify-content:center;width:100%;";

      el.parentNode.insertBefore(holder, el);
      el.style.display = "none";

      window.google.accounts.id.renderButton(holder, {
        theme: "outline",
        size: "large",
        shape: "pill",
        text: "continue_with",
        logo_alignment: "center",
        width: width,
      });
    }
  }

  function start() {
    if (!window.google || !window.google.accounts || !window.google.accounts.id) {
      report("Google Identity Services failed to load.");
      return;
    }

    window.google.accounts.id.initialize({
      client_id: CLIENT_ID,
      callback: onCredential,
      auto_select: false,
      cancel_on_tap_outside: true,
    });

    mount();

    // React re-renders on navigation and state changes, which discards our
    // button. Re-mount whenever the DOM changes.
    var pending = null;
    new MutationObserver(function () {
      if (pending) return;
      pending = window.setTimeout(function () {
        pending = null;
        mount();
      }, 150);
    }).observe(document.body, { childList: true, subtree: true });
  }

  var script = document.createElement("script");
  script.src = "https://accounts.google.com/gsi/client";
  script.async = true;
  script.defer = true;
  script.onload = start;
  script.onerror = function () {
    report("Could not load Google Identity Services.");
  };
  document.head.appendChild(script);
})();
