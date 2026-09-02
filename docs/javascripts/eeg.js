/* strands-emotiv · hero EEG traces
   Five band traces (theta→gamma) on a canvas, drawn from a static
   pseudo-signal, no network, no frameworks. Honors reduced motion
   by rendering a single still frame. Survives mkdocs-material's
   instant navigation via document$ when present. */
(function () {
  var BANDS = [
    { color: "#7c5cff", f: 0.9,  amp: 1.00, speed: 0.35 }, // theta
    { color: "#4ea8ff", f: 1.6,  amp: 0.80, speed: 0.55 }, // alpha
    { color: "#4ee1c2", f: 2.6,  amp: 0.62, speed: 0.80 }, // betaL
    { color: "#b8f25d", f: 3.9,  amp: 0.45, speed: 1.10 }, // betaH
    { color: "#ffb84e", f: 6.2,  amp: 0.30, speed: 1.60 }  // gamma
  ];

  function trace(ctx, w, h, band, row, t) {
    var mid = (row + 0.5) * (h / BANDS.length);
    var lane = h / BANDS.length * 0.38;
    ctx.beginPath();
    for (var x = 0; x <= w; x += 2) {
      var p = x / w;
      var y = mid +
        Math.sin(p * band.f * 6.283 + t * band.speed) * lane * band.amp *
        (0.6 + 0.4 * Math.sin(p * 2.1 + t * 0.21 + row)) +
        Math.sin(p * band.f * 17.0 + t * band.speed * 2.3 + row * 5) * lane * 0.18;
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = band.color;
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function boot() {
    var canvas = document.getElementById("eeg-hero");
    if (!canvas || canvas.dataset.booted) return;
    canvas.dataset.booted = "1";
    var ctx = canvas.getContext("2d");
    var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var raf = null;

    function size() {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var r = canvas.getBoundingClientRect();
      canvas.width = r.width * dpr;
      canvas.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return r;
    }

    function frame(now) {
      var r = size();
      ctx.clearRect(0, 0, r.width, r.height);
      var t = now / 1000;
      for (var i = 0; i < BANDS.length; i++) trace(ctx, r.width, r.height, BANDS[i], i, t);
      if (!reduced) raf = requestAnimationFrame(frame);
    }

    frame(performance.now());
    window.addEventListener("resize", function () {
      if (reduced) frame(performance.now());
    });
    // stop drawing when the tab is hidden
    document.addEventListener("visibilitychange", function () {
      if (reduced) return;
      if (document.hidden && raf) { cancelAnimationFrame(raf); raf = null; }
      else if (!document.hidden && !raf) raf = requestAnimationFrame(frame);
    });
  }

  if (window.document$ && window.document$.subscribe) {
    window.document$.subscribe(boot);
  } else {
    document.readyState === "loading"
      ? document.addEventListener("DOMContentLoaded", boot)
      : boot();
  }
})();
