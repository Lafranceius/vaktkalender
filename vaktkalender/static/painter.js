// The can walks around the background calendar and paints the coloured days in.
// Without JS (or with reduced motion) the days are simply shown already painted.
(() => {
  const grid = document.querySelector(".bg-cal__grid");
  const painter = document.querySelector(".painter");
  const main = document.querySelector("main");
  if (!grid || !painter) return;

  const all = [...grid.querySelectorAll(".bg-cal__cell.is-shift, .bg-cal__cell.is-today")];
  if (!all.length) {
    painter.hidden = true;
    return;
  }

  const WALK_MS = 1100;
  const PAINT_MS = 900;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let x = 0;

  // Prefer days you can actually see (not hidden behind the cards or off screen).
  function visibleCells() {
    const m = main.getBoundingClientRect();
    const seen = all.filter((cell) => {
      const r = cell.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const onScreen = cx > 0 && cy > 0 && cx < innerWidth && cy < innerHeight;
      const covered = cx > m.left && cx < m.right && cy > m.top && cy < m.bottom;
      return onScreen && !covered;
    });
    return seen.length >= 2 ? seen : all;
  }

  function stepTo(cell) {
    const nx = cell.offsetLeft - painter.offsetWidth * 0.45;
    const ny = cell.offsetTop + cell.offsetHeight - painter.offsetHeight * 0.95;
    painter.classList.toggle("is-left", nx < x);
    painter.style.transform = `translate(${nx}px, ${ny}px)`;
    x = nx;
  }

  async function run() {
    for (;;) {
      const cells = visibleCells();
      all.forEach((c) => c.classList.toggle("js-paint", cells.includes(c)));
      for (const cell of cells) {
        painter.classList.add("is-walking");
        stepTo(cell);
        await sleep(WALK_MS);
        painter.classList.remove("is-walking", "is-left");
        painter.style.setProperty("--paint", getComputedStyle(cell).getPropertyValue("--fill").trim());
        painter.classList.add("is-painting");
        cell.classList.add("is-painted");
        await sleep(PAINT_MS);
        painter.classList.remove("is-painting");
      }
      await sleep(5000);
      cells.forEach((c) => c.classList.remove("is-painted"));
      await sleep(900);
    }
  }

  const first = visibleCells()[0];
  painter.style.transition = "none";
  stepTo(first);
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return; // stay put, days stay painted
  painter.getBoundingClientRect(); // commit the start position before animating
  painter.style.transition = "";
  run();
})();
