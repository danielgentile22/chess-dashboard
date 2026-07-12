/*
 * assets/lpv-init.js — Game-detail board bootstrap (issue #60 [F6]).
 *
 * Lichess's open-source pgn-viewer ships as an ES module (bundled locally at
 * assets/lichess-pgn-viewer.min.js and kept out of Dash's classic <script>
 * auto-bundle via assets_ignore).  This classic script imports it on demand and
 * mounts it onto every `.lpv` element the Game-detail page renders, behind the
 * Game / My Analysis view switcher.
 *
 * Dash is a single-page app: it swaps page content in place without a full
 * reload, so we (re)scan on every DOM mutation, marking each mount once-only.
 */
(function () {
  "use strict";

  var lpvPromise = null;

  // The viewer REPLACES its mount element on every render, destroying the
  // data-pgn-* payload and any "already mounted" marker put on it — which made
  // the MutationObserver below re-init the board forever (a hard browser
  // hang).  So mounted-ness and the PGNs live here, keyed by the card wrapper
  // (which the viewer never touches), and `.lpv` is re-queried at use time.
  var mounted = new WeakSet();
  // The view each card is currently meant to show ("game"/"analysis"/"coach"/
  // "engine").  Set synchronously on every switch so an async board render that
  // resolves after the user has moved on can tell it's stale and bail (#93 [3]).
  var views = new WeakMap();

  function mountFor(card) {
    return card.querySelector(".lpv");
  }

  // Import the vendored ES module once; reuse the resolved default export.
  function loadLpv() {
    if (!lpvPromise) {
      lpvPromise = import("/assets/lichess-pgn-viewer.min.js").then(function (m) {
        return m.default;
      });
    }
    return lpvPromise;
  }

  // Render (or re-render) the board for the chosen view.  Re-calling the viewer
  // rebuilds the mount in place, so switching views is just a fresh mount.
  // *data* is the attribute payload captured before the first render wiped it.
  function renderBoard(card, data, view) {
    var pgn = view === "analysis" ? data.analysis
      // The Coach view (issue #74 [G4]) replays the coach's annotated line —
      // his variations and notes — in the same board, like My Analysis.
      : view === "coach" ? data.coach
      : data.game;
    loadLpv().then(function (LichessPgnViewer) {
      // The user may have switched away (e.g. to Engine) while the module import
      // was in flight; don't paint a board they no longer asked for (#93 [3]).
      if (views.get(card) !== view) {
        return;
      }
      var mount = mountFor(card);
      if (!mount) {
        return;
      }
      LichessPgnViewer(mount, {
        pgn: pgn,
        orientation: data.orientation,
        showPlayers: "auto",
        showClocks: true,
        showMoves: "auto",
        scrollToMove: true,
        drawArrows: true,
        // We provide our own "Open on Lichess" button; hide the viewer's links.
        lichess: false,
        menu: { getPgn: { enabled: false } },
      });
    }).catch(function () {
      // The import failed (asset missing after a bad deploy, network hiccup, CSP
      // blocking module scripts).  Reset the memoized promise so the next
      // interaction retries, and show a visible fallback instead of a dead blank
      // board — the "Open on Lichess" button still works alongside (#93 [2]).
      lpvPromise = null;
      if (views.get(card) !== view) {
        return;
      }
      var mount = mountFor(card);
      if (mount) {
        mount.textContent = "Board failed to load — open this game on Lichess instead.";
      }
    });
  }

  function initCard(card) {
    var mount = mountFor(card);
    if (!mount || mounted.has(card)) {
      return;
    }
    mounted.add(card);
    var data = {
      game: mount.getAttribute("data-pgn-game") || "",
      analysis: mount.getAttribute("data-pgn-analysis") || "",
      coach: mount.getAttribute("data-pgn-coach") || "",
      orientation: mount.getAttribute("data-orientation") || undefined,
    };
    views.set(card, "game");
    renderBoard(card, data, "game");

    // The Engine view (issue #63 [F7]) is server-rendered Dash content, not a
    // board — so the switcher toggles between the board mount and this panel
    // rather than re-mounting the viewer.
    var engine = card.querySelector(".lpv-engine");

    var switches = card.querySelectorAll(".lpv-switch");
    Array.prototype.forEach.call(switches, function (btn) {
      btn.addEventListener("click", function () {
        // Re-clicking the active view would re-mount and reset the replay
        // position; a no-op click should be a no-op (#93 [10]).
        if (btn.classList.contains("active")) {
          return;
        }
        Array.prototype.forEach.call(switches, function (s) {
          s.classList.remove("active");
        });
        btn.classList.add("active");

        var view = btn.getAttribute("data-view");
        views.set(card, view);  // record the desired view before any async render (#93 [3])
        var liveMount = mountFor(card);
        if (view === "engine") {
          if (liveMount) {
            liveMount.style.display = "none";
          }
          if (engine) {
            engine.style.display = "";
            // A Plotly graph laid out while its container was hidden renders at
            // zero width; nudge it to redraw now that the panel is visible.
            window.dispatchEvent(new Event("resize"));
          }
        } else {
          if (engine) {
            engine.style.display = "none";
          }
          if (liveMount) {
            liveMount.style.display = "";
          }
          renderBoard(card, data, view);
        }
      });
    });
  }

  function scan() {
    var cards = document.querySelectorAll(".game-board-card");
    Array.prototype.forEach.call(cards, initCard);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }

  // Dash mounts the Game-detail page after navigation — rescan on DOM changes.
  // The observer sees every mutation on every page (Plotly redraws on the chart
  // pages fire storms of them); coalesce a whole batch into one scan per frame so
  // board-less pages don't pay a document-wide selector query per mutation (#93 [11]).
  var scanScheduled = false;
  var observer = new MutationObserver(function () {
    if (scanScheduled) {
      return;
    }
    scanScheduled = true;
    requestAnimationFrame(function () {
      scanScheduled = false;
      scan();
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
