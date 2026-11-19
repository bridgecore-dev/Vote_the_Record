// /congress/ pages: state filter and member search on a roll call; chamber filter and side-by-side compare on
// the index. Runs only in the browser on what the page already holds: nothing is stored or sent anywhere.
(function () {
  "use strict";

  // A roll call: filter the member cards by state and by name.
  var bar = document.getElementById("member-filter");
  if (bar) {
    var state = document.getElementById("state-filter");
    var search = document.getElementById("member-search");
    var count = document.getElementById("filter-count");
    var cards = Array.prototype.slice.call(document.querySelectorAll(".member"));
    var groups = Array.prototype.slice.call(document.querySelectorAll(".vote-group"));
    var apply = function () {
      var st = state.value, q = search.value.trim().toLowerCase(), shown = 0;
      cards.forEach(function (c) {
        var ok = (!st || c.getAttribute("data-state") === st) && (!q || c.getAttribute("data-name").indexOf(q) !== -1);
        c.hidden = !ok;
        if (ok) shown += 1;
      });
      groups.forEach(function (g) {
        var any = g.querySelector(".member:not([hidden])");
        g.classList.toggle("filtered-empty", !any && !!(st || q));
      });
      count.textContent = shown + " of " + cards.length;
    };
    state.addEventListener("change", apply);
    search.addEventListener("input", apply);
    bar.hidden = false;
  }

  // The index: show one chamber or both.
  var seg = document.getElementById("chamber-filter");
  if (seg) {
    var buttons = Array.prototype.slice.call(seg.querySelectorAll("button"));
    var items = Array.prototype.slice.call(document.querySelectorAll(".vote-item"));
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var ch = b.getAttribute("data-chamber");
        buttons.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        items.forEach(function (it) { it.hidden = !!ch && it.getAttribute("data-chamber") !== ch; });
      });
    });
    seg.hidden = false;
  }

  // The index: compare two members on every roll call listed.
  var data = document.getElementById("compare-data");
  var form = document.getElementById("compare-form");
  if (data && form) {
    var d = JSON.parse(data.textContent);
    var a = document.getElementById("compare-a"), b = document.getElementById("compare-b");
    var out = document.getElementById("compare-out");
    Object.keys(d.members).forEach(function (key) {
      var m = d.members[key];
      var label = m.n + " (" + m.p + "-" + m.s + ", " + (m.c === "house" ? "House" : "Senate") + ")";
      [a, b].forEach(function (sel) {
        var o = document.createElement("option");
        o.value = key;
        o.textContent = label;
        sel.appendChild(o);
      });
    });
    var cell = function (tag, text, cls) {
      var el = document.createElement(tag);
      el.textContent = text;
      if (cls) el.className = cls;
      return el;
    };
    var draw = function () {
      out.textContent = "";
      var picked = [a.value, b.value].filter(Boolean);
      if (!picked.length) return;
      var table = document.createElement("table");
      table.className = "compare-grid";
      var head = document.createElement("tr");
      head.appendChild(cell("th", "Roll call"));
      picked.forEach(function (k) { head.appendChild(cell("th", d.members[k].n)); });
      var thead = document.createElement("thead");
      thead.appendChild(head);
      table.appendChild(thead);
      var body = document.createElement("tbody");
      d.votes.forEach(function (v) {
        var row = document.createElement("tr");
        var th = document.createElement("th");
        th.scope = "row";
        var link = document.createElement("a");
        link.href = v.url;
        link.textContent = v.label;
        th.appendChild(link);
        th.appendChild(cell("span", v.date + " · " + v.heading, "you-chose"));
        row.appendChild(th);
        picked.forEach(function (k) {
          var vote = v.v[k];
          row.appendChild(cell("td", vote || "Not in this vote", vote ? "cell" : "cell cell-none"));
        });
        if (picked.length === 2 && v.v[picked[0]] && v.v[picked[0]] === v.v[picked[1]]) row.className = "same";
        body.appendChild(row);
      });
      table.appendChild(body);
      out.appendChild(table);
    };
    a.addEventListener("change", draw);
    b.addEventListener("change", draw);
    form.hidden = false;
  }
})();
