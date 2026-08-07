/* Maps for the trip screens.
 *
 * The built app ships no map library at all, so this adds one. It is vanilla
 * DOM rather than a React component on purpose: the bundle is generated and
 * minified, its identifiers change on every build, and mounting into that tree
 * from outside would break silently. Watching for a placeholder and rendering
 * beside it is stable across rebuilds.
 *
 * Leaflet + OpenStreetMap tiles, which need NO API KEY. That is the whole
 * reason for the choice - Mapbox and Google both want a key, a billing account
 * and a domain allowlist before a single tile renders, and none of that should
 * stand between you and a working demo. OSM's tile policy asks for
 * attribution (included below) and modest volume; if this ever outgrows that,
 * swapping the tile URL for a paid provider is one line.
 *
 * Where it draws:
 *   /current-trip  -> the stops of the trip in progress, joined in day order
 *   /past-trips    -> everywhere you have been, one pin per place
 *
 * Data comes from GET /api/trips/map, which returns GeoJSON. If that call
 * fails or returns nothing, the map removes itself rather than leaving an
 * empty grey box on the page.
 */
(function () {
  "use strict";

  var LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  var LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  var TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  var ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  var TOKEN_KEY = "tio.access_token";
  var CONTAINER_ID = "tio-map";

  function token() {
    try {
      return window.localStorage.getItem(TOKEN_KEY);
    } catch (e) {
      return null;
    }
  }

  function load(url, isCss) {
    return new Promise(function (resolve, reject) {
      var existing = document.querySelector('[data-tio-map="' + url + '"]');
      if (existing) return resolve();
      var el = isCss ? document.createElement("link") : document.createElement("script");
      if (isCss) {
        el.rel = "stylesheet";
        el.href = url;
      } else {
        el.src = url;
        el.async = true;
      }
      el.setAttribute("data-tio-map", url);
      el.onload = resolve;
      el.onerror = reject;
      document.head.appendChild(el);
    });
  }

  function heading() {
    // Anchor on the page's own heading rather than on a class name - headings
    // are content, class names are build output.
    var wanted = /current trip|past trips|explorer map/i;
    var nodes = document.querySelectorAll("h1, h2");
    for (var i = 0; i < nodes.length; i++) {
      if (wanted.test(nodes[i].textContent || "")) return nodes[i];
    }
    return null;
  }

  function scope() {
    var path = window.location.pathname;
    if (path.indexOf("past-trips") !== -1) return "past";
    if (path.indexOf("current-trip") !== -1) return "current";
    return null;
  }

  function makeContainer(after) {
    var wrap = document.createElement("div");
    wrap.id = CONTAINER_ID;
    wrap.style.cssText =
      "height:340px;width:100%;margin:16px 0 24px;border-radius:6px;" +
      "overflow:hidden;border:1px solid rgba(0,0,0,.12);background:#eee";
    var host = after.parentNode;
    if (after.nextSibling) host.insertBefore(wrap, after.nextSibling);
    else host.appendChild(wrap);
    return wrap;
  }

  function fetchGeo(which) {
    var headers = { Accept: "application/json" };
    var t = token();
    if (t) headers.Authorization = "Bearer " + t;
    var url = "/api/trips/map" + (which === "past" ? "?status=past" : "?status=current");
    return fetch(url, { headers: headers }).then(function (r) {
      if (!r.ok) throw new Error("map " + r.status);
      return r.json();
    });
  }

  function draw(container, geo) {
    var features = (geo && geo.features) || [];
    if (!features.length) {
      container.parentNode.removeChild(container);
      return;
    }

    var map = L.map(container, { scrollWheelZoom: false });
    L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 18 }).addTo(map);

    var latlngs = [];
    features.forEach(function (f) {
      if (!f.geometry || f.geometry.type !== "Point") return;
      var c = f.geometry.coordinates;
      var p = f.properties || {};
      var here = [c[1], c[0]];
      latlngs.push(here);
      var label =
        "<strong>" + (p.title || p.name || "Stop") + "</strong>" +
        (p.city ? "<br>" + p.city : "") +
        (p.day ? "<br>Day " + p.day : "") +
        (p.trip_name ? "<br><em>" + p.trip_name + "</em>" : "");
      L.marker(here).addTo(map).bindPopup(label);
    });

    if (!latlngs.length) {
      container.parentNode.removeChild(container);
      return;
    }

    // Join the stops so a day reads as a route rather than scattered pins.
    if (latlngs.length > 1) {
      L.polyline(latlngs, { weight: 2, opacity: 0.6 }).addTo(map);
    }
    map.fitBounds(L.latLngBounds(latlngs).pad(0.2));
    if (latlngs.length === 1) map.setZoom(13);

    locate(map);
  }

  function locate(map) {
    // The traveller's own position, if they allow it. Asked for only once the
    // map exists, so the permission prompt has visible context - a prompt that
    // appears over a page with no map on it reads as spyware.
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var here = [pos.coords.latitude, pos.coords.longitude];
        L.circleMarker(here, {
          radius: 7,
          weight: 2,
          color: "#1d4d2b",
          fillColor: "#2f7d4f",
          fillOpacity: 0.9,
        })
          .addTo(map)
          .bindPopup("You are here");
      },
      function () {
        /* declined or unavailable - the map is still useful */
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 }
    );
  }

  function render() {
    var which = scope();
    if (!which) return;
    if (document.getElementById(CONTAINER_ID)) return;
    var anchor = heading();
    if (!anchor) return;

    var container = makeContainer(anchor);
    Promise.all([load(LEAFLET_CSS, true), load(LEAFLET_JS, false)])
      .then(function () {
        return fetchGeo(which);
      })
      .then(function (geo) {
        draw(container, geo);
      })
      .catch(function () {
        // Never leave a broken frame behind.
        if (container.parentNode) container.parentNode.removeChild(container);
      });
  }

  // The app is a single-page router, so the heading appears after navigation
  // rather than at load. Watching the document is what makes this work on a
  // client-side route change as well as a hard refresh.
  var timer = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(render, 250);
  }

  if (document.readyState !== "loading") schedule();
  else document.addEventListener("DOMContentLoaded", schedule);

  new MutationObserver(function () {
    if (!document.getElementById(CONTAINER_ID)) schedule();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
