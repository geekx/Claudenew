(function () {
  "use strict";

  var ROTATION_ANGLES = { "0": 0, "0.5": 45, "1": 180, "-0.5": 135 };
  var TEMP_THRESHOLD = 40;
  var ROW_LABELS = ["夹持状态", "工序", "产品旋转状态", "温度状态", "激光标记方向"];
  var CHEVRON_START = [242, 147, 60];
  var CHEVRON_END = [158, 158, 158];

  var PROCESSES = [
    { op: "OP010", name: "Paper insertion", rotation: 0, clamp: "00", temperature: 25, laser_mark: 0 },
    { op: "OP020", name: "Pin insertion", rotation: 0, clamp: "11", temperature: 25, laser_mark: 0 },
    { op: "OP030", name: "Trimming", rotation: 1, clamp: "10", temperature: 25, laser_mark: 1 },
    { op: "OP040", name: "Twisting", rotation: 0.5, clamp: "10", temperature: 25, laser_mark: 0 },
    { op: "OP050", name: "Widening", rotation: -0.5, clamp: "11", temperature: 25, laser_mark: 1 },
    { op: "OP060", name: "Forming cutting", rotation: 0, clamp: "00", temperature: 25, laser_mark: 0 },
    { op: "OP070", name: "Straightening", rotation: 0, clamp: "00", temperature: 25, laser_mark: 0 },
    { op: "OP080", name: "Pin forming", rotation: 1, clamp: "10", temperature: 25, laser_mark: 1 },
    { op: "OP090", name: "Laser Welding", rotation: 0, clamp: "11", temperature: 45, laser_mark: 0 },
    { op: "OP100", name: "Tig", rotation: 0, clamp: "11", temperature: 60, laser_mark: 0 },
    { op: "OP110", name: "Powder coating", rotation: 0, clamp: "00", temperature: 25, laser_mark: 1 },
    { op: "OP120", name: "preheating", rotation: 0, clamp: "00", temperature: 55, laser_mark: 0 },
    { op: "OP130", name: "Gel", rotation: 0.5, clamp: "10", temperature: 50, laser_mark: 0 },
    { op: "OP140", name: "Cure", rotation: 1, clamp: "10", temperature: 120, laser_mark: 1 },
    { op: "OP150", name: "Cooling", rotation: 0, clamp: "00", temperature: 25, laser_mark: 0 },
    { op: "OP160", name: "Tricking", rotation: -0.5, clamp: "11", temperature: 45, laser_mark: 1 },
    { op: "OP170", name: "Press", rotation: 0, clamp: "00", temperature: 25, laser_mark: 0 },
    { op: "OP180", name: "PDIV", rotation: 0, clamp: "11", temperature: 25, laser_mark: 0 },
    { op: "OP190", name: "EOL", rotation: 0, clamp: "00", temperature: 25, laser_mark: 0 }
  ];

  function cloneSvg(tplId) {
    var tpl = document.getElementById(tplId);
    var frag = document.importNode(tpl.content, true);
    return frag.querySelector("svg");
  }

  function newCell(extraClass) {
    var div = document.createElement("div");
    div.className = "cell" + (extraClass ? " " + extraClass : "");
    return div;
  }

  function caption(cell, text) {
    var c = document.createElement("div");
    c.className = "caption";
    c.textContent = text;
    cell.appendChild(c);
  }

  function lerpColor(a, b, t) {
    var r = Math.round(a[0] + (b[0] - a[0]) * t);
    var g = Math.round(a[1] + (b[1] - a[1]) * t);
    var bch = Math.round(a[2] + (b[2] - a[2]) * t);
    return "rgb(" + r + "," + g + "," + bch + ")";
  }

  function buildClampCell(code) {
    var cell = newCell();
    var tplId = code === "00" ? "tpl-clamp-00" : (code === "10" ? "tpl-clamp-ext" : "tpl-clamp-int");
    cell.appendChild(cloneSvg(tplId));
    var names = { "00": "双爪外夹", "11": "三爪内夹", "10": "三爪外夹" };
    caption(cell, code + " " + names[code]);
    return cell;
  }

  function buildChevronCell(step, colorCss) {
    var cell = newCell("chevron-cell");
    var svg = cloneSvg("tpl-chevron");
    svg.querySelector(".chevron-poly").setAttribute("fill", colorCss);
    cell.appendChild(svg);
    var label = document.createElement("div");
    label.className = "label";
    var op = document.createElement("div");
    op.className = "op";
    op.textContent = step.op;
    var name = document.createElement("div");
    name.className = "name";
    name.textContent = step.name;
    label.appendChild(op);
    label.appendChild(name);
    cell.appendChild(label);
    return cell;
  }

  function buildRotationCell(code) {
    var cell = newCell();
    var angle = ROTATION_ANGLES[String(code)];
    var svg = cloneSvg("tpl-stator");
    svg.querySelector(".stator").setAttribute("transform", "rotate(" + angle + " 89 72.5)");
    cell.appendChild(svg);
    if (angle !== 0) {
      cell.appendChild(cloneSvg("tpl-rotate-arrow"));
    }
    caption(cell, "R = " + code + "  (" + angle + "°)");
    return cell;
  }

  function buildTempCell(value) {
    var cell = newCell();
    var svg = cloneSvg("tpl-thermometer");
    var hot = value > TEMP_THRESHOLD;
    var color = hot ? "var(--red)" : "var(--blue)";
    var frac = Math.min(0.92, Math.max(0.08, value / 100));
    var stemBottom = 68, stemTop = 12;
    var mercuryHeight = frac * (stemBottom - stemTop);
    var mercuryY = stemBottom - mercuryHeight;
    var stem = svg.querySelector(".mercury-stem");
    stem.setAttribute("y", mercuryY);
    stem.setAttribute("height", mercuryHeight);
    stem.setAttribute("fill", color);
    svg.querySelector(".mercury-bulb").setAttribute("fill", color);
    cell.appendChild(svg);
    caption(cell, "T = " + value + "°C");
    return cell;
  }

  function buildLaserCell(code) {
    var cell = newCell();
    var svg = cloneSvg("tpl-laser");
    var top = svg.querySelector(".arrow-top");
    var bottom = svg.querySelector(".arrow-bottom");
    if (code === 0) {
      bottom.style.display = "none";
    } else {
      top.style.display = "none";
    }
    cell.appendChild(svg);
    caption(cell, code === 0 ? "0 · 标记于上部" : "1 · 标记于下部");
    return cell;
  }

  function buildRowLabel(text) {
    var div = document.createElement("div");
    div.className = "row-label";
    div.textContent = text;
    return div;
  }

  function render() {
    var sheet = document.getElementById("sheet");
    var n = PROCESSES.length;
    sheet.style.gridTemplateColumns = "140px repeat(" + n + ", 120px)";

    var rowBuilders = [
      function (step) { return buildClampCell(step.clamp); },
      function (step, i) { return buildChevronCell(step, lerpColor(CHEVRON_START, CHEVRON_END, i / Math.max(1, n - 1))); },
      function (step) { return buildRotationCell(step.rotation); },
      function (step) { return buildTempCell(step.temperature); },
      function (step) { return buildLaserCell(step.laser_mark); }
    ];

    for (var r = 0; r < ROW_LABELS.length; r++) {
      sheet.appendChild(buildRowLabel(ROW_LABELS[r]));
      for (var i = 0; i < n; i++) {
        sheet.appendChild(rowBuilders[r](PROCESSES[i], i));
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
