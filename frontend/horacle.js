/* Horacle UI 1.0 — barre d'écosystème commune. Source : Horacle Orchestrator/design/horacle-ui/ (copie exacte
   dans chaque projet). Injecte en haut de <body> la même barre partout : marque, liens vers chaque application,
   heure. L'application courante est reconnue par son port (ou <script data-app="…">). Aucune requête réseau. */
(function () {
  if (window.__horacleEco) return;
  window.__horacleEco = true;
  var APPS = [
    { id: "orchestrator", name: "Dashboard", port: "8700" },
    { id: "thanos", name: "Thanos", port: "8050" },
    { id: "proplab", name: "PropFirm Research Lab", port: "8830" },
    { id: "research", name: "Research", port: "3001" },
    { id: "apply", name: "Apply", port: "8002" },
    { id: "hub", name: "Hub", port: "8000" },
  ];
  var me = document.currentScript && document.currentScript.dataset.app;
  if (!me) {
    var port = location.port;
    var hit = APPS.find(function (a) { return a.port === port; });
    me = hit ? hit.id : port === "5173" ? "apply" : "";
  }
  function build() {
    if (!document.body || document.querySelector(".ho-eco")) return;
    var bar = document.createElement("div");
    bar.className = "ho-eco";
    bar.setAttribute("role", "navigation");
    bar.setAttribute("aria-label", "Écosystème Horacle");
    var html = '<a class="ho-mark" href="http://127.0.0.1:8700/" title="Horacle Orchestrator"><i></i>HORACLE</a><span class="ho-eco-sep"></span>';
    APPS.forEach(function (a) {
      html += '<a href="http://127.0.0.1:' + a.port + '/"' + (a.id === me ? ' class="on" aria-current="page"' : "") + ">" + a.name + "</a>";
    });
    html += '<span class="ho-eco-right" data-ho-clock></span>';
    bar.innerHTML = html;
    document.body.insertBefore(bar, document.body.firstChild);
    var clock = bar.querySelector("[data-ho-clock]");
    var tick = function () {
      var d = new Date();
      clock.textContent = d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" }) + " · " +
        d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 15000);
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
