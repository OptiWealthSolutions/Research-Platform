/* Horacle UI 1.2 — barre d'écosystème commune. Source : Horacle Orchestrator/design/horacle-ui/ (copie exacte
   dans chaque projet). Injecte en haut de <body> la MÊME barre partout : marque et sections du desk.
   Une section mène soit à une vue de l'orchestrateur (:8700/#…), soit directement à une application.
   La section active est reconnue par le port (ou <script data-app="…">) et, dans l'orchestrateur, par l'ancre.
   Aucune requête réseau, pas d'horloge (l'heure est affichée une seule fois, sur le Dashboard).
   1.2 : le Digest vit dans le Dashboard ; Macro et Recherche côte à côte ; barre plus haute. */
(function () {
  if (window.__horacleEco) return;
  window.__horacleEco = true;
  var ORCH = "http://127.0.0.1:8700/";
  var ITEMS = [
    { id: "overview", label: "Dashboard", hash: "" },
    { id: "macro", label: "Macro", hash: "macro", ports: ["8000"] },
    { id: "recherche", label: "Recherche", url: "http://127.0.0.1:3001/", ports: ["3001", "8001"] },
    { id: "live", label: "Live", hash: "live" },
    { id: "strategies", label: "Stratégies", url: "http://127.0.0.1:8050/", ports: ["8050"] },
    { id: "propfirm", label: "PropFirm", url: "http://127.0.0.1:8830/", ports: ["8830"] },
    { id: "perso", label: "Perso", hash: "perso", ports: ["8002", "5173"] },
    { id: "systeme", label: "Système", hash: "systeme" },
  ];
  // Ancres de l'orchestrateur (anciennes comprises) → section de la barre
  var ALIAS = { "": "overview", overview: "overview", macro: "macro", hub: "macro", live: "live", track: "live",
    strategies: "strategies", thanos: "strategies", proplab: "propfirm", recherche: "recherche", research: "recherche",
    digest: "overview", perso: "perso", os: "perso", apply: "perso", systeme: "systeme", code: "systeme", journal: "systeme" };
  var APP_PORT = { orchestrator: "8700", thanos: "8050", proplab: "8830", research: "3001", apply: "8002", hub: "8000" };
  var me = document.currentScript && document.currentScript.dataset.app;
  var port = me && APP_PORT[me] ? APP_PORT[me] : location.port;
  var inOrch = port === "8700" || me === "orchestrator";
  function current() {
    if (inOrch) return ALIAS[location.hash.slice(1).split("/")[0]] || "overview";
    var hit = ITEMS.find(function (i) { return (i.ports || []).indexOf(port) >= 0; });
    return hit ? hit.id : "";
  }
  function href(i) {
    if (i.url) return i.url;
    return inOrch ? "#" + (i.hash || "overview") : ORCH + (i.hash ? "#" + i.hash : "");
  }
  function build() {
    if (!document.body || document.querySelector(".ho-eco")) return;
    var bar = document.createElement("div");
    bar.className = "ho-eco";
    bar.setAttribute("role", "navigation");
    bar.setAttribute("aria-label", "Écosystème Horacle");
    var html = '<a class="ho-mark" href="' + (inOrch ? "#overview" : ORCH) + '" title="Horacle Orchestrator"><i></i>HORACLE</a><span class="ho-eco-sep"></span><nav class="ho-eco-nav">';
    ITEMS.forEach(function (i) { html += '<a href="' + href(i) + '" data-ho-item="' + i.id + '">' + i.label + "</a>"; });
    html += '</nav><span class="ho-eco-right" data-ho-slot></span>';
    bar.innerHTML = html;
    document.body.insertBefore(bar, document.body.firstChild);
    var mark = function () {
      var cur = current();
      bar.querySelectorAll("[data-ho-item]").forEach(function (a) {
        var on = a.getAttribute("data-ho-item") === cur;
        a.classList.toggle("on", on);
        if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
    };
    mark();
    window.addEventListener("hashchange", mark);
    document.documentElement.classList.add("ho-has-eco");
    if (me) document.documentElement.dataset.hoApp = me;
    function syncHeaderHeight() {
      var hdr = document.querySelector("body > .ho-header, .page > .ho-header, .page > .header");
      if (!hdr) return false;
      var update = function () {
        var h = hdr.getBoundingClientRect().height;
        document.documentElement.style.setProperty("--ho-header-h", h.toFixed(2) + "px");
      };
      update();
      if (window.ResizeObserver && !hdr.__hoObserved) {
        hdr.__hoObserved = true;
        new ResizeObserver(update).observe(hdr);
      }
      return true;
    }
    if (!syncHeaderHeight() && !document.querySelector(".site-header, header.sticky") && window.MutationObserver) {
      var mo = new MutationObserver(function () {
        if (syncHeaderHeight()) mo.disconnect();
      });
      mo.observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
