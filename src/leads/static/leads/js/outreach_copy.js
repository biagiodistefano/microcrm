/* Copy-to-clipboard for outreach message text (no external dependencies). */
(function () {
  "use strict";

  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(area);
    return ok;
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return fallbackCopy(text); }
      );
    }
    return Promise.resolve(fallbackCopy(text));
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".js-copy-text");
    if (!button) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    var original = button.textContent;
    copyText(button.getAttribute("data-copy-text") || "").then(function (ok) {
      button.textContent = ok ? "Copied!" : "Copy failed";
      window.setTimeout(function () { button.textContent = original; }, 1500);
    });
  });
})();
