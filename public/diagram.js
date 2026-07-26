// Embedded editable diagram block for the CoCo-doc editor.
//
// Philosophy borrowed from icebird1998/scientific-illustrator: a diagram
// embedded in a document should stay a set of native, editable objects,
// not get flattened into a raster image. Shapes here are plain data
// (rect/ellipse/arrow/text) rendered as SVG and serialized into the
// block's `data-diagram` attribute, so the diagram round-trips through
// the same innerHTML-based save/load path as the rest of the document.
window.CocoDiagram = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  let uidCounter = 0;
  function uid(prefix) {
    return `${prefix}-${Date.now().toString(36)}-${(++uidCounter).toString(36)}`;
  }

  function createDefaultData() {
    return {
      w: 480,
      h: 260,
      shapes: [
        { id: uid('shape'), type: 'rect', x: 40, y: 40, w: 150, h: 70, text: 'Box', fill: '#eef2ff', stroke: '#4338ca' },
      ],
    };
  }

  function clampSize(v) {
    return Math.max(20, v);
  }

  function shapeBBox(shape) {
    if (shape.type === 'arrow') {
      return {
        x: Math.min(shape.x1, shape.x2),
        y: Math.min(shape.y1, shape.y2),
        w: Math.abs(shape.x2 - shape.x1) || 1,
        h: Math.abs(shape.y2 - shape.y1) || 1,
      };
    }
    return { x: shape.x, y: shape.y, w: shape.w, h: shape.h };
  }

  // Builds a fresh, fully interactive diagram block from `data`. Used both
  // for a brand-new insert and for re-hydrating a block loaded from saved
  // document HTML (where the markup is static but the data survives in
  // data-diagram).
  function build(data, onChange) {
    data = data || createDefaultData();
    const wrap = document.createElement('div');
    wrap.className = 'diagram-block';
    wrap.contentEditable = 'false';

    const toolbar = document.createElement('div');
    toolbar.className = 'diagram-toolbar';
    toolbar.innerHTML = `
      <button type="button" data-add="rect" title="Add rectangle">&#9645; Rect</button>
      <button type="button" data-add="ellipse" title="Add ellipse">&#9711; Ellipse</button>
      <button type="button" data-add="arrow" title="Add arrow">&#8599; Arrow</button>
      <button type="button" data-add="text" title="Add text label">T Text</button>
      <button type="button" data-action="delete" title="Delete selected shape">Delete</button>
    `;
    wrap.appendChild(toolbar);

    const svg = document.createElementNS(NS, 'svg');
    svg.classList.add('diagram-canvas');
    svg.setAttribute('viewBox', `0 0 ${data.w} ${data.h}`);
    svg.setAttribute('width', data.w);
    svg.setAttribute('height', data.h);
    wrap.appendChild(svg);

    const markerId = uid('arrowhead');

    let selectedId = null;
    let drag = null; // { id, mode: 'move' | 'resize', startX, startY, orig }

    function persist() {
      wrap.dataset.diagram = JSON.stringify(data);
      if (onChange) onChange();
    }

    function svgPoint(evt) {
      const rect = svg.getBoundingClientRect();
      const scaleX = data.w / rect.width;
      const scaleY = data.h / rect.height;
      return {
        x: (evt.clientX - rect.left) * scaleX,
        y: (evt.clientY - rect.top) * scaleY,
      };
    }

    function render() {
      svg.innerHTML = '';
      const defs = document.createElementNS(NS, 'defs');
      defs.innerHTML = `<marker id="${markerId}" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="#374151" /></marker>`;
      svg.appendChild(defs);
      for (const shape of data.shapes) {
        svg.appendChild(renderShape(shape));
      }
      wrap.dataset.diagram = JSON.stringify(data);
    }

    function renderShape(shape) {
      const g = document.createElementNS(NS, 'g');
      g.dataset.shapeId = shape.id;
      g.classList.add('diagram-shape');
      if (shape.id === selectedId) g.classList.add('selected');

      if (shape.type === 'rect') {
        const rect = document.createElementNS(NS, 'rect');
        rect.setAttribute('x', shape.x);
        rect.setAttribute('y', shape.y);
        rect.setAttribute('width', shape.w);
        rect.setAttribute('height', shape.h);
        rect.setAttribute('rx', 6);
        rect.setAttribute('fill', shape.fill);
        rect.setAttribute('stroke', shape.stroke);
        rect.setAttribute('stroke-width', 2);
        g.appendChild(rect);
        g.appendChild(labelEl(shape));
        g.appendChild(resizeHandle(shape));
      } else if (shape.type === 'ellipse') {
        const el = document.createElementNS(NS, 'ellipse');
        el.setAttribute('cx', shape.x + shape.w / 2);
        el.setAttribute('cy', shape.y + shape.h / 2);
        el.setAttribute('rx', shape.w / 2);
        el.setAttribute('ry', shape.h / 2);
        el.setAttribute('fill', shape.fill);
        el.setAttribute('stroke', shape.stroke);
        el.setAttribute('stroke-width', 2);
        g.appendChild(el);
        g.appendChild(labelEl(shape));
        g.appendChild(resizeHandle(shape));
      } else if (shape.type === 'arrow') {
        const line = document.createElementNS(NS, 'line');
        line.setAttribute('x1', shape.x1);
        line.setAttribute('y1', shape.y1);
        line.setAttribute('x2', shape.x2);
        line.setAttribute('y2', shape.y2);
        line.setAttribute('stroke', '#374151');
        line.setAttribute('stroke-width', 2.5);
        line.setAttribute('marker-end', `url(#${markerId})`);
        g.appendChild(line);
        const hit = document.createElementNS(NS, 'line');
        hit.setAttribute('x1', shape.x1);
        hit.setAttribute('y1', shape.y1);
        hit.setAttribute('x2', shape.x2);
        hit.setAttribute('y2', shape.y2);
        hit.setAttribute('stroke', 'transparent');
        hit.setAttribute('stroke-width', 14);
        hit.classList.add('diagram-hit');
        g.appendChild(hit);
      } else if (shape.type === 'text') {
        const bg = document.createElementNS(NS, 'rect');
        bg.setAttribute('x', shape.x);
        bg.setAttribute('y', shape.y);
        bg.setAttribute('width', shape.w);
        bg.setAttribute('height', shape.h);
        bg.setAttribute('fill', 'transparent');
        bg.classList.add('diagram-hit');
        g.appendChild(bg);
        g.appendChild(labelEl(shape));
        g.appendChild(resizeHandle(shape));
      }

      g.addEventListener('mousedown', (evt) => onShapeMouseDown(evt, shape));
      g.addEventListener('dblclick', (evt) => {
        evt.stopPropagation();
        editLabel(shape);
      });
      return g;
    }

    function labelEl(shape) {
      const text = document.createElementNS(NS, 'text');
      const box = shapeBBox(shape);
      text.setAttribute('x', box.x + box.w / 2);
      text.setAttribute('y', box.y + box.h / 2);
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('dominant-baseline', 'middle');
      text.classList.add('diagram-label');
      text.textContent = shape.text || '';
      return text;
    }

    function resizeHandle(shape) {
      const handle = document.createElementNS(NS, 'rect');
      handle.setAttribute('x', shape.x + shape.w - 6);
      handle.setAttribute('y', shape.y + shape.h - 6);
      handle.setAttribute('width', 12);
      handle.setAttribute('height', 12);
      handle.classList.add('diagram-resize-handle');
      handle.addEventListener('mousedown', (evt) => {
        evt.stopPropagation();
        selectedId = shape.id;
        drag = { id: shape.id, mode: 'resize', start: svgPoint(evt), orig: { ...shape } };
      });
      return handle;
    }

    function editLabel(shape) {
      const next = window.prompt('Label text:', shape.text || '');
      if (next === null) return;
      shape.text = next;
      render();
      persist();
    }

    function onShapeMouseDown(evt, shape) {
      evt.stopPropagation();
      selectedId = shape.id;
      drag = { id: shape.id, mode: 'move', start: svgPoint(evt), orig: { ...shape } };
      render();
    }

    function onMouseMove(evt) {
      if (!drag) return;
      const shape = data.shapes.find((s) => s.id === drag.id);
      if (!shape) return;
      const p = svgPoint(evt);
      const dx = p.x - drag.start.x;
      const dy = p.y - drag.start.y;
      if (shape.type === 'arrow') {
        shape.x1 = drag.orig.x1 + dx;
        shape.y1 = drag.orig.y1 + dy;
        shape.x2 = drag.orig.x2 + dx;
        shape.y2 = drag.orig.y2 + dy;
      } else if (drag.mode === 'resize') {
        shape.w = clampSize(drag.orig.w + dx);
        shape.h = clampSize(drag.orig.h + dy);
      } else {
        shape.x = drag.orig.x + dx;
        shape.y = drag.orig.y + dy;
      }
      render();
    }

    function onMouseUp() {
      if (drag) persist();
      drag = null;
    }

    svg.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);

    svg.addEventListener('mousedown', () => {
      selectedId = null;
      render();
    });

    toolbar.addEventListener('mousedown', (evt) => evt.stopPropagation());
    toolbar.addEventListener('click', (evt) => {
      const addBtn = evt.target.closest('button[data-add]');
      if (addBtn) {
        const type = addBtn.dataset.add;
        const base = { id: uid('shape'), x: 30, y: 30 };
        let shape;
        if (type === 'rect') {
          shape = { ...base, type: 'rect', w: 140, h: 70, text: 'Box', fill: '#eef2ff', stroke: '#4338ca' };
        } else if (type === 'ellipse') {
          shape = { ...base, type: 'ellipse', w: 120, h: 90, text: 'Node', fill: '#ecfdf5', stroke: '#047857' };
        } else if (type === 'arrow') {
          shape = { id: uid('shape'), type: 'arrow', x1: 30, y1: 30, x2: 170, y2: 30 };
        } else {
          shape = { ...base, type: 'text', w: 120, h: 30, text: 'Label' };
        }
        data.shapes.push(shape);
        selectedId = shape.id;
        render();
        persist();
        return;
      }
      const actionBtn = evt.target.closest('button[data-action="delete"]');
      if (actionBtn && selectedId) {
        data.shapes = data.shapes.filter((s) => s.id !== selectedId);
        selectedId = null;
        render();
        persist();
      }
    });

    render();
    return wrap;
  }

  // Re-creates an interactive block from a static one found in loaded
  // document HTML (its data-diagram attribute survived innerHTML
  // round-tripping, but its event listeners did not).
  function hydrateAll(container, onChange) {
    container.querySelectorAll('.diagram-block[data-diagram]').forEach((el) => {
      let data;
      try {
        data = JSON.parse(el.dataset.diagram);
      } catch {
        return;
      }
      el.replaceWith(build(data, onChange));
    });
  }

  return { build, createDefaultData, hydrateAll };
})();
