/*
 * Avatar drag-and-drop shim.
 *
 * The deployed React bundle only offers a "Picture URL" text field - its own
 * hint says "File uploads are coming later". This replaces it with a drop zone
 * (and a click-to-browse fallback), POSTs the file to /api/profile/avatar, and
 * writes the URL the server returns back into the field.
 *
 * "Replaces" is display:none, not removal. The input is where the React
 * component keeps its state and what "Save profile" reads, so it has to stay
 * in the tree - it just stops being something the user sees or types into.
 * Rebuilding the frontend is the only way to actually delete it.
 *
 * Saving is left to the app. Pressing "Save profile" persists the picture
 * through the normal PATCH /api/profile - a stored upload is not the same as
 * the user agreeing to put it on their profile.
 *
 * STOPGAP: index.html is build output. The next frontend deploy overwrites it
 * and drops the <script> tag below. Either re-add it, or build a real file
 * input into the frontend source and delete this file.
 */
(function () {
  "use strict";

  var TOKEN_KEY = "tio.access_token"; // must match the app's key
  var ENDPOINT = "/api/profile/avatar";
  var MAX_BYTES = 5 * 1024 * 1024; // must match avatars.MAX_BYTES
  var TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];

  var IDLE = "Drag a photo here, or click to choose one.";
  var FORMATS = "JPEG, PNG, GIF or WebP — up to 5 MB.";
  var CAPTION = "Profile picture";
  var ZONE_CLASS =
    "mt-2 flex w-full cursor-pointer items-center justify-center gap-2 " +
    "rounded-xl border border-dashed border-input bg-background px-4 py-3 " +
    "text-xs font-medium text-muted-foreground transition-colors";

  // Labels whose drag listeners are already attached. Keyed on the element, so
  // a label React throws away is not kept alive by this.
  var bound = new WeakSet();

  // One picker for the whole page, retargeted per click - creating one per
  // field would leave a dead <input> behind on every re-render.
  var picker = null;
  var target = null;

  /*
   * The field is React-controlled, so assigning .value is invisible to it -
   * React's value tracker sees no change and reverts on the next render.
   * Going through the prototype setter defeats the tracker, and the bubbling
   * "input" event is what React's onChange actually listens for.
   */
  function setReactValue(input, value) {
    var descriptor = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    );
    if (descriptor && descriptor.set) {
      descriptor.set.call(input, value);
    } else {
      input.value = value;
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  /*
   * Matched on visible text, because the build output has no stable id or data
   * attribute to hook onto - and then marked, because rewriting the caption
   * destroys the very text that found it. A label React replaces wholesale
   * loses the mark but gets the original caption back, so one of the two
   * always matches.
   */
  function pictureFields() {
    var found = [];
    var labels = document.querySelectorAll("label");

    for (var i = 0; i < labels.length; i++) {
      var label = labels[i];
      var claimed = label.getAttribute("data-tio-avatar") === "1";
      var named =
        (label.textContent || "").toLowerCase().indexOf("picture url") !== -1;
      if (!claimed && !named) continue;

      var input = label.querySelector('input[type="text"], input:not([type])');
      if (input) found.push({ label: label, input: input });
    }
    return found;
  }

  function zoneIn(label) {
    return label.querySelector("[data-tio-drop]");
  }

  function describe(zone, message, tone) {
    if (!zone) return;
    zone.textContent = message;
    zone.style.borderColor = tone === "error" ? "currentColor" : "";
    zone.style.opacity = tone === "busy" ? "0.6" : "";
  }

  function reject(file) {
    // An empty type happens with odd sources; let the server's magic-byte
    // check be the judge rather than refusing a valid image here.
    if (file.type && !/^image\//.test(file.type)) {
      return "That is not an image. Use a JPEG, PNG, GIF or WebP.";
    }
    if (file.size > MAX_BYTES) {
      return "That image is larger than 5 MB. Try a smaller one.";
    }
    if (file.size === 0) {
      return "That file is empty.";
    }
    return null;
  }

  // FastAPI sends {"detail": "..."} for our own errors and {"detail": [...]}
  // for request-validation failures. Only the former is worth showing.
  function detailOf(body, status) {
    if (body && typeof body.detail === "string") return body.detail;
    if (status === 401) return "Your session expired. Sign in again.";
    if (status === 413) return "That image is too large.";
    return "Upload failed (" + status + "). Try again.";
  }

  function upload(file, input, zone) {
    var problem = reject(file);
    if (problem) {
      describe(zone, problem, "error");
      return;
    }

    var token = window.localStorage.getItem(TOKEN_KEY);
    if (!token) {
      describe(zone, "Sign in before changing your picture.", "error");
      return;
    }

    var form = new FormData();
    form.append("file", file, file.name || "avatar");

    describe(zone, "Uploading " + (file.name || "image") + "…", "busy");

    fetch(ENDPOINT, {
      method: "POST",
      // No Content-Type header: the browser must set the multipart boundary.
      headers: { Authorization: "Bearer " + token, Accept: "application/json" },
      body: form,
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
          describe(zone, detailOf(result.body, result.status), "error");
          return;
        }
        var url = result.body && result.body.avatar_url;
        if (!url) {
          describe(zone, "The server returned no URL. Try again.", "error");
          return;
        }
        // Hand it to React; the preview and the Save button take it from here.
        setReactValue(input, url);
        describe(zone, "Ready — press Save profile to keep it.");
      })
      .catch(function (err) {
        describe(zone, "Could not reach the server: " + err.message, "error");
      });
  }

  function ensurePicker() {
    if (picker) return picker;

    picker = document.createElement("input");
    picker.type = "file";
    picker.accept = TYPES.join(",");
    picker.style.display = "none";

    picker.addEventListener("change", function () {
      var file = picker.files && picker.files[0];
      // Reset first, so re-picking the same file still fires "change".
      picker.value = "";
      if (file && target) upload(file, target.input, target.zone);
    });

    document.body.appendChild(picker);
    return picker;
  }

  function makeZone(entry) {
    var zone = document.createElement("button");
    zone.type = "button";
    zone.setAttribute("data-tio-drop", "1");
    zone.className = ZONE_CLASS;
    zone.textContent = IDLE;

    zone.addEventListener("click", function (event) {
      // The label wraps the text field, so without this the click also lands
      // there and yanks focus away mid-interaction.
      event.preventDefault();
      event.stopPropagation();
      target = { input: entry.input, zone: zone };
      ensurePicker().click();
    });

    // Above the help text, where the input used to be, rather than below it.
    var spans = entry.label.querySelectorAll("span");
    var hint = spans.length > 1 ? spans[spans.length - 1] : null;
    if (hint) {
      entry.label.insertBefore(zone, hint);
    } else {
      entry.label.appendChild(zone);
    }
    return zone;
  }

  /*
   * Take the URL field out of the interface. It stays in the tree - the React
   * component keeps its state there and "Save profile" reads it - but the user
   * only ever deals with the drop zone.
   *
   * Re-applied on every mount pass, because a re-render restores the
   * component's own markup and with it the field and its old caption.
   */
  function hideUrlField(entry) {
    entry.label.setAttribute("data-tio-avatar", "1");
    if (entry.input.style.display !== "none") {
      entry.input.style.display = "none";
    }

    var spans = entry.label.querySelectorAll("span");
    if (!spans.length) return;

    // The caption is an icon element plus a text node; assigning textContent
    // would take the icon with it, so only the text node is rewritten.
    var caption = spans[0];
    for (var i = 0; i < caption.childNodes.length; i++) {
      var node = caption.childNodes[i];
      if (node.nodeType === 3 && /picture url/i.test(node.nodeValue)) {
        node.nodeValue = CAPTION;
      }
    }

    var hint = spans[spans.length - 1];
    if (hint !== caption && hint.textContent !== FORMATS) {
      hint.textContent = FORMATS;
    }
  }

  // Drag handlers live on the label, not the zone, so the whole field is a
  // target - aiming at a 40px strip is a nuisance. They look the zone up at
  // event time because React may have replaced it since.
  function bindDragTarget(entry) {
    var label = entry.label;

    function highlight(event) {
      event.preventDefault();
      event.stopPropagation();
      var zone = zoneIn(label);
      if (zone) {
        zone.style.borderColor = "currentColor";
        zone.style.opacity = "1";
      }
    }

    function clear(event) {
      event.preventDefault();
      event.stopPropagation();
      var zone = zoneIn(label);
      if (zone) zone.style.borderColor = "";
    }

    label.addEventListener("dragenter", highlight);
    label.addEventListener("dragover", highlight);
    label.addEventListener("dragleave", clear);
    label.addEventListener("drop", function (event) {
      event.preventDefault();
      event.stopPropagation();
      var zone = zoneIn(label);
      if (zone) zone.style.borderColor = "";
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files[0]) {
        upload(files[0], entry.input, zone);
      } else {
        describe(zone, "That drop had no file in it.", "error");
      }
    });
  }

  function mount() {
    pictureFields().forEach(function (entry) {
      // The zone's presence is the test, not a marker attribute: React can
      // discard our nodes on re-render while keeping the label itself, and a
      // marker would make a stripped field look already handled.
      if (!zoneIn(entry.label)) makeZone(entry);
      hideUrlField(entry);
      if (!bound.has(entry.label)) {
        bindDragTarget(entry);
        bound.add(entry.label);
      }
    });
  }

  function start() {
    /*
     * Dropping a file anywhere the page does not handle makes the browser
     * navigate to it, discarding the app's state and anything typed.
     * Cancelling the default keeps a near-miss harmless; element handlers
     * still run, because they fire before this one on the way up.
     */
    ["dragover", "drop"].forEach(function (type) {
      document.addEventListener(type, function (event) {
        var types = event.dataTransfer && event.dataTransfer.types;
        if (types && Array.prototype.indexOf.call(types, "Files") !== -1) {
          event.preventDefault();
        }
      });
    });

    mount();

    // The profile screen mounts after hydration and re-renders on navigation,
    // so wait for the field rather than assuming it is present now.
    var pending = null;
    new MutationObserver(function () {
      if (pending) return;
      pending = window.setTimeout(function () {
        pending = null;
        mount();
      }, 150);
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
