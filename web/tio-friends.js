/* A Friends section on the socials screen.
 *
 * The built app has no such section: it shows "Travellers near you", and once
 * you follow someone they leave that list (correctly - the only button on the
 * card is Follow, and pressing it again does nothing) and then appear nowhere
 * at all. So following someone made them vanish, and there was no route from
 * the socials screen to a conversation with them.
 *
 * Rendered as plain DOM beneath the suggestions, for the same reason as
 * tio-map.js: the bundle is minified, its identifiers change on every build,
 * and mounting into that React tree from outside would break silently. This
 * anchors on the heading text, which is content rather than build output.
 *
 * Data comes from GET /api/users/suggested, whose `connections` array carries
 * accepted friends and people you have followed but who have not answered yet.
 * "Chat" creates the conversation and goes straight to it - which works
 * because the chat guard now accepts a pending request as well as a friendship.
 */
(function () {
  "use strict";

  var SECTION_ID = "tio-friends";
  var TOKEN_KEY = "tio.access_token";

  function token() {
    try {
      return window.localStorage.getItem(TOKEN_KEY);
    } catch (e) {
      return null;
    }
  }

  function api(path, options) {
    var opts = options || {};
    var headers = { Accept: "application/json" };
    if (opts.body) headers["Content-Type"] = "application/json";
    var t = token();
    if (t) headers.Authorization = "Bearer " + t;
    return fetch("/api" + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.status === 204 ? null : r.json();
    });
  }

  function suggestionsHeading() {
    var nodes = document.querySelectorAll("h1, h2, h3");
    for (var i = 0; i < nodes.length; i++) {
      if (/travellers near you/i.test(nodes[i].textContent || "")) return nodes[i];
    }
    return null;
  }

  function initials(name) {
    return (name || "?")
      .split(/\s+/)
      .slice(0, 2)
      .map(function (w) { return w.charAt(0).toUpperCase(); })
      .join("");
  }

  function avatar(person) {
    var box = document.createElement("div");
    box.style.cssText =
      "width:42px;height:42px;border-radius:4px;flex:0 0 42px;overflow:hidden;" +
      "display:flex;align-items:center;justify-content:center;" +
      "background:#e7e3d8;color:#2c3b2c;font-size:13px;font-weight:600";
    if (person.avatarUrl || person.avatar_url) {
      var img = document.createElement("img");
      img.src = person.avatarUrl || person.avatar_url;
      img.alt = "";
      img.loading = "lazy";
      img.style.cssText = "width:100%;height:100%;object-fit:cover";
      box.appendChild(img);
    } else {
      box.textContent = initials(person.name);
    }
    return box;
  }

  function row(person) {
    var li = document.createElement("li");
    li.style.cssText =
      "display:flex;align-items:center;gap:12px;padding:12px 14px;" +
      "border:1px solid rgba(0,0,0,.10);border-radius:4px;background:#fdfcf8";

    li.appendChild(avatar(person));

    var text = document.createElement("div");
    text.style.cssText = "flex:1 1 auto;min-width:0";
    var name = document.createElement("p");
    name.textContent = person.name || "Traveller";
    name.style.cssText = "margin:0;font-size:14px;font-weight:500";
    var sub = document.createElement("p");
    var pending = person.friendshipStatus === "pending_out";
    sub.textContent = pending
      ? "Request sent - you can still start a chat"
      : person.handle
      ? "@" + person.handle
      : "Connected";
    sub.style.cssText = "margin:2px 0 0;font-size:12px;color:#6b6a60";
    text.appendChild(name);
    text.appendChild(sub);
    li.appendChild(text);

    var chat = document.createElement("button");
    chat.type = "button";
    chat.textContent = "Chat";
    chat.style.cssText =
      "flex:0 0 auto;border:0;border-radius:4px;padding:7px 14px;cursor:pointer;" +
      "background:#1d4d2b;color:#fff;font-size:12px;font-weight:600";
    chat.addEventListener("click", function () {
      chat.disabled = true;
      chat.textContent = "Opening...";
      api("/conversations", {
        method: "POST",
        body: { name: person.name || "Chat", member_ids: [person.id] },
      })
        .then(function () {
          window.location.href = "/chat";
        })
        .catch(function () {
          // Most likely an existing chat or a rejected member; the chat screen
          // is still the right place to land, and it will show what exists.
          window.location.href = "/chat";
        });
    });
    li.appendChild(chat);
    return li;
  }

  function render(people) {
    var anchor = suggestionsHeading();
    if (!anchor) return;

    var existing = document.getElementById(SECTION_ID);
    if (existing) existing.parentNode.removeChild(existing);

    var section = document.createElement("section");
    section.id = SECTION_ID;
    section.style.cssText = "margin:28px 0 8px";

    var title = document.createElement("h2");
    title.textContent = "Friends";
    title.style.cssText = "margin:0;font-size:26px;font-weight:700";
    var caption = document.createElement("p");
    caption.textContent = people.length
      ? "People you follow. Start a chat with any of them."
      : "Nobody yet - follow a traveller above and they will appear here.";
    caption.style.cssText = "margin:4px 0 14px;font-size:14px;color:#6b6a60";
    section.appendChild(title);
    section.appendChild(caption);

    if (people.length) {
      var list = document.createElement("ul");
      list.style.cssText = "list-style:none;margin:0;padding:0;display:grid;gap:10px";
      people.forEach(function (p) { list.appendChild(row(p)); });
      section.appendChild(list);
    }

    // After the whole suggestions block, not immediately after the heading -
    // otherwise it lands between the heading and its own list.
    var block = anchor.parentNode;
    if (block && block.parentNode) block.parentNode.insertBefore(section, block.nextSibling);
    else anchor.parentNode.insertBefore(section, anchor.nextSibling);
  }

  function refresh() {
    if (!/socials/i.test(window.location.pathname)) return;
    if (!suggestionsHeading()) return;
    if (document.getElementById(SECTION_ID)) return;

    api("/users/suggested")
      .then(function (data) {
        render((data && data.connections) || []);
      })
      .catch(function () {
        /* leave the page as it was */
      });
  }

  var timer = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  }

  if (document.readyState !== "loading") schedule();
  else document.addEventListener("DOMContentLoaded", schedule);

  new MutationObserver(function () {
    if (!document.getElementById(SECTION_ID)) schedule();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
