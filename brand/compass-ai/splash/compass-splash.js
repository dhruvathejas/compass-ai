/*!
 * Compass AI splash — dependency-free, ~5s.
 * The mark draws itself, a few greetings cycle beside the wordmark, then the
 * colour fades into the app while the logo glides into a slot you choose.
 *
 *   CompassSplash.play({
 *     assetBase: "/static/brand/compass-ai/",  // folder holding logo/ and fonts/
 *     target: "#brand-slot",                    // optional: where the logo lands
 *     once: "compass-splash-seen",              // optional: sessionStorage key, plays once per tab
 *     onDone: () => {}
 *   });
 */
(function (root) {
  "use strict";

  var REST = 30; // resting needle angle (deg), must match logo/mark.svg
  var GREETINGS = ["G'Day", "Cuppa Tea?", "Cheers", "Howdy", "Ready when you are", "Hello there"];
  var NATURAL_W = 180 + 36 + 329, NATURAL_H = 230; // lockup + greeting room, px at --k:1

  var MARK =
    '<svg class="cs-mark" viewBox="0 0 180 180" aria-hidden="true">' +
    '<circle class="cs-dr cs-d1" cx="90" cy="90" r="76" pathLength="1" stroke-width="3.5"/>' +
    '<circle class="cs-dr cs-d2" cx="90" cy="90" r="64" pathLength="1" stroke-width="1.8"/>' +
    '<path class="cs-dr cs-d2" d="M90 8v10M90 162v10M8 90h10M162 90h10" pathLength="1" stroke-width="2.5"/>' +
    '<g class="cs-nd"><path class="cs-fill" d="M90 34L103 90H77z"/>' +
    '<path class="cs-dr cs-d3" d="M90 34L103 90L90 146L77 90Z" pathLength="1" stroke-width="3.5"/></g>' +
    '<circle class="cs-dr cs-d3" cx="90" cy="90" r="4" pathLength="1" stroke-width="2.5"/></svg>';

  function shuffle(list, random) {
    var a = list.slice();
    if (random) for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html) e.innerHTML = html;
    return e;
  }

  function play(options) {
    var o = Object.assign({
      assetBase: "./",
      greetings: GREETINGS,
      count: 3,          // how many greetings to cycle
      random: true,      // shuffle the list each launch
      target: null,      // selector or element the logo glides into
      once: null,        // sessionStorage key: skip if already seen this tab
      background: "#F7F4EE",
      paused: false,     // build the timeline but don't run it (used by the GIF exporter)
      onDone: null
    }, options || {});

    if (o.once) {
      try { if (sessionStorage.getItem(o.once)) { o.onDone && o.onDone(); return Promise.resolve(); } } catch (e) {}
    }

    var reduce = root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var target = typeof o.target === "string" ? document.querySelector(o.target) : o.target;
    var base = o.assetBase.replace(/\/?$/, "/");

    var overlay = el("div", "cs-overlay");
    overlay.setAttribute("aria-hidden", "true");
    var bg = el("div", "cs-bg");
    bg.style.setProperty("--cs-bg", o.background);
    var brand = el("div", "cs-brand", MARK);
    var word = el("img", "cs-word");
    word.alt = ""; word.src = base + "logo/word.svg";
    var greets = el("div", "cs-greets");
    var list = shuffle(o.greetings, o.random).slice(0, o.count);
    list.forEach(function (g) { var s = el("span", "cs-g"); s.textContent = g; greets.appendChild(s); });
    brand.appendChild(word); brand.appendChild(greets);
    overlay.appendChild(bg); overlay.appendChild(brand);

    var k = Math.min(1, (root.innerWidth - 48) / NATURAL_W, (root.innerHeight - 48) / NATURAL_H);
    brand.style.setProperty("--k", Math.max(k, 0.3));

    var fonts = document.fonts && document.fonts.load
      ? Promise.race([document.fonts.load("600 40px 'Compass Caveat'"), new Promise(function (r) { setTimeout(r, 800); })])
      : Promise.resolve();
    var img = word.decode ? word.decode().catch(function () {}) : Promise.resolve();

    return Promise.all([fonts, img]).then(function () {
      document.body.appendChild(overlay);
      if (target) target.style.opacity = "0";

      var anims = [];
      var A = function (node, frames, timing) {
        var a = node.animate(frames, Object.assign({ fill: "both" }, timing));
        anims.push(a); return a;
      };
      var T_EXIT = 4000, T_END;

      if (reduce) {
        T_END = 900;
        A(bg, [{ opacity: 1 }, { opacity: 0 }], { delay: 600, duration: 300 });
        A(brand, [{ opacity: 1 }, { opacity: 0 }], { delay: 600, duration: 300 });
      } else {
        var draw = function (sel, delay) {
          overlay.querySelectorAll(sel).forEach(function (n) {
            A(n, [{ strokeDasharray: 1, strokeDashoffset: 1 }, { strokeDasharray: 1, strokeDashoffset: 0 }],
              { delay: delay, duration: 900, easing: "ease-in-out" });
          });
        };
        draw(".cs-d1", 0); draw(".cs-d2", 250); draw(".cs-d3", 500);
        A(overlay.querySelector(".cs-fill"), [{ opacity: 0 }, { opacity: 1 }], { delay: 1100, duration: 400 });
        var r = function (d) { return "rotate(" + (REST + d) + "deg)"; };
        A(overlay.querySelector(".cs-nd"),
          [{ transform: r(0), offset: 0 }, { transform: r(14), offset: .25 }, { transform: r(-14), offset: .55 },
           { transform: r(6), offset: .8 }, { transform: r(0), offset: 1 }],
          { delay: 1400, duration: 2400, easing: "ease-in-out" });
        A(word, [{ opacity: 0, transform: "translateX(-14px)" }, { opacity: 1, transform: "none" }],
          { delay: 400, duration: 700, easing: "ease-out" });
        greets.querySelectorAll(".cs-g").forEach(function (g, i) {
          A(g, [{ opacity: 0, transform: "translateY(10px)" },
                { opacity: 1, transform: "none", offset: .2 },
                { opacity: 1, transform: "none", offset: .8 },
                { opacity: 0, transform: "translateY(-6px)" }],
            { delay: 900 + i * 1000, duration: 1000 });
        });

        // exit: colour dissolves into the app, logo glides to its slot (or fades)
        A(bg, [{ opacity: 1 }, { opacity: 0 }], { delay: T_EXIT + 100, duration: 800 });
        if (target) {
          var tr = target.getBoundingClientRect(), br = brand.getBoundingClientRect();
          // the lockup (mark + word) is what lands; greetings are gone by then
          var kk = parseFloat(brand.style.getPropertyValue("--k")) || 1;
          var bw = (180 + 36 + 329) * kk, bh = 180 * kk;
          var s = Math.min(tr.width / bw, tr.height / bh);
          var dx = tr.left + (tr.width - bw * s) / 2 - br.left;
          var dy = tr.top + (tr.height - bh * s) / 2 - br.top;
          A(brand, [{ transform: "none" }, { transform: "translate(" + dx + "px," + dy + "px) scale(" + s + ")" }],
            { delay: T_EXIT, duration: 900, easing: "cubic-bezier(.65,0,.3,1)" });
        } else {
          A(brand, [{ opacity: 1 }, { opacity: 0 }], { delay: T_EXIT + 300, duration: 600 });
        }
        T_END = T_EXIT + 900;
      }

      var onKey = function (e) { if (e.key === "Escape" || e.key === "Enter" || e.key === " ") finish(); };
      var finish = function () {
        if (!overlay.parentNode) return;
        root.removeEventListener("keydown", onKey);
        overlay.remove();
        if (target) target.style.opacity = "";
        if (o.once) { try { sessionStorage.setItem(o.once, "1"); } catch (e) {} }
        o.onDone && o.onDone();
      };

      overlay.addEventListener("pointerdown", finish);
      root.addEventListener("keydown", onKey);

      if (o.paused) {
        anims.forEach(function (a) { a.pause(); a.currentTime = 0; });
        return { duration: T_END, animations: anims, finish: finish };
      }
      return Promise.all(anims.map(function (a) { return a.finished; })).then(finish, finish);
    });
  }

  root.CompassSplash = { play: play, greetings: GREETINGS, duration: 4900 };
})(window);
