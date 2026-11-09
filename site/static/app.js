// Counterproof site: certificate tabs (with the stamp re-inked on each switch), copy buttons
// and the theme toggle. Everything works without JavaScript except the tabs, which then
// show the first certificate.
(function () {
  "use strict";

  var tabs = Array.prototype.slice.call(document.querySelectorAll('.sheet [role="tab"]'));
  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (!panel) return;
      panel.hidden = !on;
      if (on) {
        var stamp = panel.querySelector(".stamp");
        if (stamp) { stamp.style.animation = "none"; void stamp.offsetWidth; stamp.style.animation = ""; }
      }
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

  var root = document.documentElement;
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
