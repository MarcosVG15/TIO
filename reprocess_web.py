"""Re-apply the patches a fresh frontend deploy wipes.

    python reprocess_web.py

Run this after every drop-in build. `web/index.html` is build output, so
anything hand-added to it survives exactly until the next deploy - this script
is the record of what has to go back, and it is idempotent, so running it twice
is harmless.

What it restores, and why each one cannot live in the frontend source instead:

  1. Google sign-in shim. The build still has no VITE_GOOGLE_CLIENT_ID baked in
     and never loads Google Identity Services, so its own button throws
     "Google sign-in is not configured yet". `google-signin.js` supplies both.
     Delete this step once the frontend build sets the client ID.

  2. Travelpayouts "Drive" tag, in <head> on every page. The app is a
     single-page app - Caddy serves this one file for every route - so one
     insertion here genuinely is every page.

  3. canonical / og:url. The build bakes in the Lovable preview domain, which
     as a cross-domain canonical tells Google to de-index tio.agency. It has to
     be fixed in the bundle as well as the HTML, because the router re-applies
     its own head config on hydration and would otherwise revert the tag.

Deliberately no longer applied: the avatar drag-and-drop shim. The frontend now
ships a real uploader posting to /api/profile/avatar, so `avatar-upload.js` is
dead - its hook ("Picture URL") no longer exists in the build.
"""

from __future__ import annotations

import glob
import os
import re
import sys

#: Caddy serves this directory as the document root, which is why this script
#: lives outside it - anything in there is downloadable by the public.
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

#: Injected just before </body>.
BODY_TAGS = [
    '<script src="/google-signin.js" defer></script>',
]

#: Injected just before </head>. Travelpayouts asks for it in <head> on every
#: page; kept verbatim as they supply it, including the cache-plugin opt-out
#: attributes, which are inert here but harmless.
HEAD_TAGS = [
    (
        # Fade every page banner into the background.
        #
        # The builder renders all of them through one component - a <section
        # class="relative z-10 leading-[0]"> wrapping a full-width <img> - with
        # no fade of its own. The banners that look faded have the gradient
        # painted into the JPEG (that is what "-flush-" means in their file
        # names); the illustrations served from the builder's CDN do not, so
        # they stop dead against the page. One mask on the shared component
        # gives every banner the same soft edge whatever the file does.
        #
        # Scoped to that section rather than to `img`, because the same scene
        # files are also used as small cards on the landing page and masking
        # those would look broken. A mask rather than an overlay so it works on
        # any background colour without hard-coding the cream.
        "tio-banner-fade",
        "<style id=\"tio-banner-fade\">"
        "section[class*='leading-[0]']>img{"
        "-webkit-mask-image:linear-gradient(to bottom,#000 0%,#000 58%,transparent 100%);"
        "mask-image:linear-gradient(to bottom,#000 0%,#000 58%,transparent 100%);"
        "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;"
        "-webkit-mask-size:100% 100%;mask-size:100% 100%;"
        "}"
        "</style>"
    ),
    (
        # Level the "Choose this option" buttons across the three suggestion
        # cards. Each card is only as tall as its own itinerary, so a plan with
        # fewer stops puts its button halfway up the row. Stretching the grid
        # items and pushing the last child down costs nothing and needs no
        # knowledge of the card's internals - which matters, because those
        # class names are minified and change every build.
        "tio-card-levelling",
        "<style id=\"tio-card-levelling\">"
        "[class*='grid-cols-']>*{align-self:stretch}"
        "[class*='grid-cols-']>*>[class*='flex-col']{height:100%}"
        "[class*='grid-cols-']>*>[class*='flex-col']>button:last-child,"
        "[class*='grid-cols-']>*>[class*='flex-col']>a:last-child{margin-top:auto}"
        "</style>"
    ),
    (
        "emrldco.com",
        '<script nowprocket data-noptimize="1" data-cfasync="false" '
        'data-wpfc-render="false" seraph-accel-crit="1" data-no-defer="1" '
        'data-cmp-ab="2">'
        "(function(){var script=document.createElement(\"script\");"
        "script.async=1;script.setAttribute(\"data-cmp-ab\",\"2\");"
        "script.src='https://emrldco.com/NTU5MDg5.js?t=559089';"
        "document.head.appendChild(script);})();"
        "</script>"
    ),
]

PREVIEW_DOMAIN = "https://tio-journey-planner.lovable.app/"
LIVE_DOMAIN = "https://tio.agency/"

#: Behavioural fixes applied to the built bundle: (file glob, pattern, repl,
#: description). Only for things the server genuinely cannot fix - a bug in
#: what the page does after a successful API call is not something a status
#: code can reach.
JS_PATCHES = [
    (
        "assets/drafts-*.js",
        # Deleting a draft succeeds server-side (204) and the card stays on
        # screen, because the mutation's success handler is empty and nothing
        # refetches the list. The user has to reload to see their own delete.
        # Matched by shape rather than by identifier: the minifier renames
        # everything on every build.
        re.compile(r"(mutationFn:\(\)=>\w+\(\w+\.id\),onSuccess:\(\)=>)\{\}"),
        r"\1{window.location.reload()}",
        "refresh the drafts list after a delete",
    ),
]


def apply_js_patches() -> int:
    """Re-apply behavioural fixes to the rebuilt bundle.

    Idempotent: each pattern only matches the unpatched form, so running twice
    changes nothing the second time. A pattern that stops matching is reported
    rather than passed over - it means the builder changed that code and the
    fix needs revisiting, which is exactly the kind of thing that otherwise
    goes unnoticed until a user complains.
    """
    applied = 0
    for pattern_glob, pattern, replacement, description in JS_PATCHES:
        targets = glob.glob(pattern_glob)
        if not targets:
            print(f"  no file matches {pattern_glob} - skipped: {description}")
            continue
        for path in targets:
            with open(path, encoding="utf-8", errors="ignore") as handle:
                source = handle.read()
            patched, count = pattern.subn(replacement, source)
            if not count:
                if replacement.split("{", 1)[-1][:20] in source:
                    print(f"  already patched  {os.path.basename(path)}  {description}")
                else:
                    print(f"  PATTERN MISSED   {os.path.basename(path)}  {description}")
                continue
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(patched)
            print(f"  patched ({count})    {os.path.basename(path)}  {description}")
            applied += count
    return applied

#: Image references worth chasing. The builder emits illustrations under its
#: own CDN prefix and does not always include the files in the export - the
#: bundle asks for /__l5e/assets-v1/<uuid>/tio-scene-swiss.jpeg, the file is
#: not there, and the page renders alt text where a picture should be.
_IMAGE_REF = re.compile(
    r"""["'`(]((?:/__l5e|/assets|/a)/[^"'`()\s]*?\.(?:png|jpe?g|webp|svg|avif))"""
)


def _referenced_images() -> set[str]:
    """Every same-origin image path the built site asks for."""
    found: set[str] = set()
    targets = ["index.html"]
    if os.path.isdir("assets"):
        targets += [
            os.path.join("assets", name)
            for name in os.listdir("assets")
            if name.endswith((".js", ".css"))
        ]
    for path in targets:
        try:
            with open(path, encoding="utf-8", errors="ignore") as handle:
                found.update(_IMAGE_REF.findall(handle.read()))
        except OSError:
            continue
    return found


def fetch_missing_images() -> int:
    """Download images the export left behind, from the preview that still has them.

    The preview site is the only place these files exist: they are not in the
    repository, not in the drop-in, and not reproducible locally. Fetching them
    is therefore not a workaround but the only way to have them at all - and it
    has to happen after every build, because each build mints new uuids for new
    illustrations.

    Missing files are reported rather than skipped silently. A page rendering
    alt text is the kind of fault that survives to production precisely because
    nothing fails when it happens.
    """
    missing = sorted(
        ref for ref in _referenced_images()
        if not os.path.exists(ref.lstrip("/"))
    )
    if not missing:
        return 0

    try:
        import httpx
    except ImportError:
        print(f"  {len(missing)} image(s) missing; install httpx to fetch them")
        for ref in missing:
            print(f"    missing  {ref}")
        return len(missing)

    failed = 0
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for ref in missing:
            local = ref.lstrip("/")
            url = PREVIEW_DOMAIN.rstrip("/") + "/" + local
            try:
                response = client.get(url)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                print(f"    FAILED   {ref}  ({type(exc).__name__})")
                failed += 1
                continue
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            with open(local, "wb") as handle:
                handle.write(response.content)
            print(f"  fetched   {ref}  ({len(response.content):,} bytes)")
    return failed


def main() -> int:
    os.chdir(WEB)

    if not os.path.isfile("index.html"):
        print(f"no index.html in {WEB}")
        return 1

    with open("index.html", encoding="utf-8") as handle:
        html = handle.read()

    if "</body>" not in html or "</head>" not in html:
        print("index.html has no </head> or </body> - refusing to patch")
        return 1

    changed = False

    for marker, tag in HEAD_TAGS:
        if marker in html:
            print(f"already present   <head>  {marker}")
        else:
            html = html.replace("</head>", tag + "</head>", 1)
            print(f"added             <head>  {marker}")
            changed = True

    for tag in BODY_TAGS:
        if tag in html:
            print(f"already present   <body>  {tag[:52]}...")
        else:
            html = html.replace("</body>", tag + "</body>", 1)
            print(f"added             <body>  {tag[:52]}...")
            changed = True

    count = html.count(PREVIEW_DOMAIN)
    if count:
        html = html.replace(PREVIEW_DOMAIN, LIVE_DOMAIN)
        print(f"rewrote domain    index.html  ({count})")
        changed = True

    if changed:
        with open("index.html", "w", encoding="utf-8", newline="") as handle:
            handle.write(html)

    # The router re-applies canonical/og:url from the bundle after hydration,
    # so patching only the HTML would let the tag revert in the browser.
    for path in sorted(glob.glob("assets/*.js")):
        with open(path, encoding="utf-8", errors="strict") as handle:
            text = handle.read()
        if PREVIEW_DOMAIN not in text:
            continue
        occurrences = text.count(PREVIEW_DOMAIN)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text.replace(PREVIEW_DOMAIN, LIVE_DOMAIN))
        print(f"rewrote domain    {path}  ({occurrences})")

    # A script tag pointing at a file that is not deployed is a 404 on every
    # page load, so check rather than assume.
    for tag in BODY_TAGS:
        match = re.search(r'src="/([^"]+)"', tag)
        if match and not os.path.isfile(match.group(1)):
            print(f"WARNING: {match.group(1)} is referenced but missing")
            return 1

    leftover = 0
    for root, _, files in os.walk("."):
        for name in files:
            try:
                with open(os.path.join(root, name), encoding="utf-8") as handle:
                    leftover += handle.read().count("lovable.app")
            except (UnicodeDecodeError, OSError):
                pass
    print(f"\nremaining lovable.app references: {leftover}")

    apply_js_patches()

    # After the domain rewrite, so a fetch is never attempted against a URL
    # this script has just pointed at the live site.
    still_missing = fetch_missing_images()
    if still_missing:
        print(f"\n{still_missing} image(s) could not be fetched - pages using "
              "them will render alt text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
