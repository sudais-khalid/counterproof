// Counterproof site: tab sets (certificates, install commands), copy buttons, the chart
// strict/lenient switch, scroll reveals and the theme toggle. Without JavaScript every
// section is visible, the first tab of each set shows, and the charts show strict values.
(function () {
  "use strict";
  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // -- tabs (WAI-ARIA tabs pattern: arrows, Home, End) ---------------------------------
  function tabSet(container) {
    var tabs = Array.prototype.slice.call(container.querySelectorAll('[role="tab"]'));
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (!panel) return;
        panel.hidden = !on;
        var stamp = on && panel.querySelector(".stamp");
        if (stamp) { stamp.style.animation = "none"; void stamp.offsetWidth; stamp.style.animation = ""; }
      });
      if (focus) tab.focus();
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { select(tab, false); });
      tab.addEventListener("keydown", function (e) {
        var next = null;
        if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") next = tabs[0];
        else if (e.key === "End") next = tabs[tabs.length - 1];
        if (next) { e.preventDefault(); select(next, true); }
      });
    });
  }
  document.querySelectorAll(".sheet, [data-install]").forEach(tabSet);

  // -- copy buttons ---------------------------------------------------------------------
  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var text = button.getAttribute("data-copy");
      var done = function () {
        button.setAttribute("data-copied", "");
        button.textContent = "Copied";
        setTimeout(function () { button.removeAttribute("data-copied"); button.textContent = "Copy"; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      }
    });
  });

  // -- charts: strict / lenient -----------------------------------------------------------
  document.querySelectorAll("[data-charts]").forEach(function (charts) {
    var buttons = Array.prototype.slice.call(charts.querySelectorAll("[data-mode]"));
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        var mode = button.getAttribute("data-mode");
        buttons.forEach(function (b) { b.setAttribute("aria-pressed", b === button ? "true" : "false"); });
        charts.querySelectorAll(".chart__row").forEach(function (row) {
          var v = row.getAttribute("data-" + mode);
          var text = row.getAttribute("data-" + mode + "-text");
          row.querySelector(".chart__bar").style.setProperty("--v", v);
          row.querySelector("[data-value]").textContent = text.split(" · ")[0];
          var label = row.querySelector(".chart__label").textContent;
          row.querySelector("[data-tip]").textContent = label + ": " + text;
        });
      });
    });
  });

  // -- reveal on scroll -------------------------------------------------------------------
  var revealed = document.querySelectorAll(".reveal");
  if (!reduceMotion && "IntersectionObserver" in window && revealed.length) {
    root.classList.add("js");
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var siblings = Array.prototype.indexOf.call(entry.target.parentNode.children, entry.target);
        entry.target.style.transitionDelay = Math.min(siblings, 4) * 60 + "ms";
        entry.target.classList.add("is-in");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
    revealed.forEach(function (el) { observer.observe(el); });
  }

  // -- theme ------------------------------------------------------------------------------
  try {
    var saved = localStorage.getItem("cp-theme");
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) { /* storage unavailable: follow the system theme */ }
  var toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var current = root.getAttribute("data-theme") ||
        (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("cp-theme", next); } catch (e) { /* not persisted */ }
    });
  }
})();
