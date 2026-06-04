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

  // Import the vendored ES module once; reuse the resolved default export.
  function loadLpv() {
    if (!lpvPromise) {
      lpvPromise = import("/assets/lichess-pgn-viewer.min.js").then(function (m) {
        return m.default;
      });
    }
    return lpvPromise;
  }

  function pgnFor(mount, view) {
    if (view === "analysis") {
      return mount.getAttribute("data-pgn-analysis") || "";
    }
    return mount.getAttribute("data-pgn-game") || "";
  }

  // Render (or re-render) the board for the chosen view.  Re-calling the viewer
  // rebuilds the mount in place, so switching views is just a fresh mount.
  function renderBoard(mount, view) {
    var orientation = mount.getAttribute("data-orientation") || undefined;
    loadLpv().then(function (LichessPgnViewer) {
      LichessPgnViewer(mount, {
        pgn: pgnFor(mount, view),
        orientation: orientation,
        showPlayers: "auto",
        showClocks: true,
        showMoves: "auto",
        scrollToMove: true,
        drawArrows: true,
        // We provide our own "Open on Lichess" button; hide the viewer's links.
        lichess: false,
        menu: { getPgn: { enabled: false } },
      });
    });
  }

  function initCard(card) {
    var mount = card.querySelector(".lpv");
    if (!mount || mount.dataset.lpvReady) {
      return;
    }
    mount.dataset.lpvReady = "1";
    renderBoard(mount, "game");

    var switches = card.querySelectorAll(".lpv-switch");
    Array.prototype.forEach.call(switches, function (btn) {
      btn.addEventListener("click", function () {
        Array.prototype.forEach.call(switches, function (s) {
          s.classList.remove("active");
        });
        btn.classList.add("active");
        renderBoard(mount, btn.getAttribute("data-view"));
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
  var observer = new MutationObserver(function () {
    scan();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
