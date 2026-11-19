// Night/day toggle. Starts from the device setting (prefers-color-scheme) and lasts only for this page view:
// nothing is stored (no cookies, no storage), so every page opens in the device's own setting.
(function () {
  "use strict";
  var button = document.getElementById("theme-toggle");
  if (!button) return;
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function current() {
    return root.getAttribute("data-theme") || (media && media.matches ? "dark" : "light");
  }
  function label() {
    var dark = current() === "dark";
    button.textContent = dark ? "Day" : "Night";
    button.setAttribute("aria-label", dark ? "Switch to light colors" : "Switch to dark colors");
  }
  button.addEventListener("click", function () {
    root.setAttribute("data-theme", current() === "dark" ? "light" : "dark");
    label();
  });
  if (media && media.addEventListener) media.addEventListener("change", label);
  button.hidden = false;
  label();
})();
