"""Deterministic frame(t) bridge for local HTML scenes embedded in film.html.

Only project-local relative references are rewritten. The injected bridge is a
small, versioned postMessage contract: the parent asks for an exact local time,
the child awaits window.film.ready, invokes window.film.frame(t), and reports
whether the page actually supports the contract.
"""
from __future__ import annotations

import html
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit


_SCENE_BRIDGE = r"""
(function () {
  'use strict';
  if (window.__videoDirectorSceneBridge) return;
  window.__videoDirectorSceneBridge = true;

  var epoch = 1767225600000;
  var clock = { time: 0, active: false, queue: [], nextId: 0, cancelled: Object.create(null), randomState: 1 };
  var NativeDate = window.Date;
  var nativeRaf = window.requestAnimationFrame.bind(window);
  var nativeCancelRaf = window.cancelAnimationFrame.bind(window);
  var nativePerformanceNow = window.performance && window.performance.now
    ? window.performance.now.bind(window.performance) : function () { return clock.time * 1000; };

  function FilmDate() {
    var args = Array.prototype.slice.call(arguments);
    if (!(this instanceof FilmDate)) {
      return args.length ? NativeDate.apply(null, args) : new NativeDate(epoch + clock.time * 1000).toString();
    }
    return args.length ? new (Function.prototype.bind.apply(NativeDate, [null].concat(args)))()
      : new NativeDate(epoch + clock.time * 1000);
  }
  FilmDate.prototype = NativeDate.prototype;
  try { Object.setPrototypeOf(FilmDate, NativeDate); } catch (_) {}
  FilmDate.now = function () { return epoch + clock.time * 1000; };
  FilmDate.parse = NativeDate.parse;
  FilmDate.UTC = NativeDate.UTC;
  window.Date = FilmDate;

  try {
    Object.defineProperty(window.performance, 'now', {
      configurable: true, value: function () { return clock.time * 1000; }
    });
  } catch (_) {}

  function seed(value) {
    clock.randomState = ((Math.floor(value * 1000000) >>> 0) ^ 0x6D2B79F5) >>> 0;
    if (!clock.randomState) clock.randomState = 1;
  }
  Math.random = function () {
    var t = clock.randomState += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  window.requestAnimationFrame = function (callback) {
    if (!clock.active) return nativeRaf(callback);
    var id = ++clock.nextId;
    clock.queue.push({ id: id, callback: callback });
    return id;
  };
  window.cancelAnimationFrame = function (id) {
    if (!clock.active) return nativeCancelRaf(id);
    clock.cancelled[id] = true;
    clock.queue = clock.queue.filter(function (item) { return item.id !== id; });
  };

  function waitNativeFrame() {
    return new Promise(function (resolve) { nativeRaf(function () { resolve(); }); });
  }
  function syncAnimations(t) {
    if (!document.getAnimations) return;
    document.getAnimations().forEach(function (animation) {
      try {
        animation.pause();
        animation.currentTime = t * 1000;
      } catch (_) {}
    });
  }
  async function waitUntilLoaded() {
    if (document.readyState !== 'complete') {
      await new Promise(function (resolve) { window.addEventListener('load', resolve, { once: true }); });
    }
    if (window.film && window.film.ready && typeof window.film.ready.then === 'function') {
      await window.film.ready;
    }
  }
  async function waitForVisualResources() {
    var waits = [];
    if (document.fonts && document.fonts.ready) {
      waits.push(new Promise(function (resolve, reject) {
        var timer = setTimeout(function () {
          reject(new Error('字体资源等待超过 12 秒'));
        }, 12000);
        Promise.resolve(document.fonts.ready).then(function () {
          clearTimeout(timer);
          resolve();
        }, function (error) {
          clearTimeout(timer);
          reject(error);
        });
      }));
    }
    Array.prototype.forEach.call(document.images || [], function (image) {
      if (image.complete && image.naturalWidth > 0) {
        if (typeof image.decode === 'function') waits.push(image.decode());
        return;
      }
      if (image.complete && image.naturalWidth === 0) {
        waits.push(Promise.reject(new Error('图片资源加载失败：' + (image.currentSrc || image.src || '<unknown>'))));
        return;
      }
      waits.push(new Promise(function (resolve, reject) {
        var timer = setTimeout(function () {
          cleanup();
          reject(new Error('图片资源等待超过 12 秒：' + (image.currentSrc || image.src || '<unknown>')));
        }, 12000);
        function cleanup() {
          clearTimeout(timer);
          image.removeEventListener('load', loaded);
          image.removeEventListener('error', failed);
        }
        function loaded() { cleanup(); resolve(); }
        function failed() {
          cleanup();
          reject(new Error('图片资源加载失败：' + (image.currentSrc || image.src || '<unknown>')));
        }
        image.addEventListener('load', loaded, { once: true });
        image.addEventListener('error', failed, { once: true });
      }));
    });
    // CSS background/mask/border/list/cursor images are not exposed through
    // document.images. Preload each computed local url() and wait for decoding
    // as well, so the renderer never screenshots a visually incomplete scene.
    var cssImageUrls = Object.create(null);
    var cssUrlPattern = /url\(\s*(['"]?)(.*?)\1\s*\)/gi;
    function rememberCssUrls(value) {
      var textValue = String(value || ''), match;
      cssUrlPattern.lastIndex = 0;
      while ((match = cssUrlPattern.exec(textValue))) {
        var url = String(match[2] || '').trim();
        if (!url || url.charAt(0) === '#' || /^(?:data:|blob:|javascript:)/i.test(url)) continue;
        cssImageUrls[url] = true;
      }
    }
    try {
      Array.prototype.forEach.call(document.querySelectorAll('*'), function (element) {
        ['', '::before', '::after', '::marker'].forEach(function (pseudo) {
          var style = pseudo ? window.getComputedStyle(element, pseudo) : window.getComputedStyle(element);
          ['backgroundImage','maskImage','webkitMaskImage','borderImageSource',
           'listStyleImage','cursor','content','filter','clipPath'].forEach(function (property) {
            rememberCssUrls(style[property]);
          });
        });
      });
    } catch (error) {
      waits.push(Promise.reject(new Error('无法检查场景 CSS 图片资源：' + String(error && error.message || error))));
    }
    // Frames repeatedly visit the same stylesheet URLs. Reuse the first
    // load/decode promise per URL instead of issuing a second Image request for
    // every output frame. A rejected promise stays rejected for this scene page:
    // a missing asset must not turn into a later silent blank frame.
    var cssResourcePromises = window.__filmCssResourcePromises;
    if (!cssResourcePromises || typeof cssResourcePromises !== 'object' || Array.isArray(cssResourcePromises)) {
      cssResourcePromises = Object.create(null);
      window.__filmCssResourcePromises = cssResourcePromises;
    }
    Object.keys(cssImageUrls).forEach(function (url) {
      if (!cssResourcePromises[url]) {
        cssResourcePromises[url] = new Promise(function (resolve, reject) {
          var image = new Image(), timer = 0, settled = false;
          function cleanup() {
            if (timer) clearTimeout(timer);
            image.removeEventListener('load', loaded);
            image.removeEventListener('error', failed);
          }
          function finishError(message) {
            if (settled) return;
            settled = true;
            cleanup();
            reject(new Error(message + '：' + url.slice(0, 500)));
          }
          function loaded() {
            if (settled) return;
            if (!image.naturalWidth || !image.naturalHeight) {
              finishError('CSS 图片资源未成功解码');
              return;
            }
            if (typeof image.decode === 'function') {
              image.decode().then(function () {
                if (settled) return;
                settled = true; cleanup(); resolve();
              }, function (error) {
                finishError('CSS 图片解码失败：' + String(error && error.message || error));
              });
            } else {
              settled = true; cleanup(); resolve();
            }
          }
          function failed() { finishError('CSS 图片资源加载失败'); }
          timer = setTimeout(function () { finishError('CSS 图片资源等待超过 12 秒'); }, 12000);
          image.addEventListener('load', loaded, { once: true });
          image.addEventListener('error', failed, { once: true });
          image.src = url;
          if (image.complete) {
            if (image.naturalWidth > 0) loaded();
            else failed();
          }
        });
      }
      waits.push(cssResourcePromises[url]);
    });
    await Promise.all(waits);
  }
  async function settleFrameResult(result, t) {
    if (!result || typeof result.then !== 'function') {
      flushRaf(t);
      return;
    }
    var settled = false, failure = null;
    Promise.resolve(result).then(function () { settled = true; }, function (error) {
      failure = error; settled = true;
    });
    // The frame promise may itself await requestAnimationFrame. Drive the
    // virtual queue while allowing microtasks and ordinary async resource work
    // to settle; native RAF is used only as a scheduler, never as film time.
    for (var attempt = 0; attempt < 300 && !settled; attempt++) {
      flushRaf(t);
      await Promise.resolve();
      if (!settled) {
        await new Promise(function (resolve) { nativeRaf(function () { resolve(); }); });
      }
    }
    if (!settled) throw new Error('frame(t) Promise 未在受控帧调度窗口内完成；请确保异步动画使用受控 RAF 或 ready 协议');
    if (failure) throw failure;
    flushRaf(t);
  }
  function flushRaf(t) {
    clock.active = true;
    var rounds = 0, callbackError = null;
    while (clock.queue.length && rounds < 8) {
      rounds += 1;
      var current = clock.queue.splice(0);
      current.forEach(function (item) {
        if (clock.cancelled[item.id]) return;
        try { item.callback(t * 1000); } catch (error) {
          if (!callbackError) callbackError = error;
        }
      });
    }
    var pending = clock.queue.length;
    clock.queue.length = 0;
    clock.cancelled = Object.create(null);
    if (callbackError) throw callbackError;
    if (pending) {
      throw new Error(
        '受控 requestAnimationFrame 连续调度超过 8 轮；请让 frame(t) 在给定时间直接绘制完整画面，避免依赖持续运行的动画循环'
      );
    }
  }
  var inspectMode = false;
  var currentOverrides = [];
  var reportedOverrideWarnings = Object.create(null);

  function reportOverrideWarning(warning, selector) {
    warning = String(warning || '未知 overrides 警告').slice(0, 1000);
    selector = typeof selector === 'string' ? selector.slice(0, 500) : '';
    var key = warning + '\\n' + selector;
    if (reportedOverrideWarnings[key]) return;
    reportedOverrideWarnings[key] = true;
    try {
      if (window.console && typeof window.console.warn === 'function') {
        window.console.warn('[film.html overrides] ' + warning + (selector ? ' [' + selector + ']' : ''));
      }
    } catch (_) {}
    try {
      parent.postMessage({type:'film-override-warning',warning:warning,selector:selector}, '*');
    } catch (_) {}
  }

  var overrideStyleMap = {
    color: 'color', backgroundColor: 'background-color', fontSize: 'font-size',
    fontWeight: 'font-weight', textAlign: 'text-align', lineHeight: 'line-height',
    letterSpacing: 'letter-spacing', left: 'left', top: 'top', width: 'width',
    height: 'height', position: 'position', opacity: 'opacity', transform: 'transform',
    borderRadius: 'border-radius', rotate: 'rotate'
  };
  function parseOverrides(value) {
    if (value == null || value === '') return [];
    if (typeof value === 'string') {
      try { value = JSON.parse(value); }
      catch (error) { throw new Error('overrides 不是有效 JSON：' + String(error && error.message || error)); }
    }
    if (!Array.isArray(value)) throw new Error('overrides 必须是 JSON 数组');
    if (value.length > 500) throw new Error('overrides 超过 500 项限制');
    var parsed = [];
    value.forEach(function (item, index) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) {
        throw new Error('overrides 第 ' + (index + 1) + ' 项必须是对象');
      }
      if (!Object.prototype.hasOwnProperty.call(item, 'selector')) {
        reportOverrideWarning('遗留记录不含 selector；已保留原值但当前 Runtime 不执行', '');
        return;
      }
      if (typeof item.selector !== 'string' || !item.selector.trim() || item.selector.length > 500
          || /[\x00-\x1f]/.test(item.selector)) {
        throw new Error('overrides 第 ' + (index + 1) + ' 项的 CSS selector 无效');
      }
      if (Object.prototype.hasOwnProperty.call(item, 'text')
          && (typeof item.text !== 'string' || item.text.length > 4000)) {
        throw new Error('overrides selector ' + item.selector + ' 的 text 必须是 4000 字符以内的字符串');
      }
      if (item.style != null && (typeof item.style !== 'object' || Array.isArray(item.style))) {
        throw new Error('overrides selector ' + item.selector + ' 的 style 必须是对象');
      }
      var style = item.style && typeof item.style === 'object' ? item.style : {};
      var executableStyle = Object.keys(style).some(function (key) {
        return Object.prototype.hasOwnProperty.call(overrideStyleMap, key);
      });
      if (!Object.prototype.hasOwnProperty.call(item, 'text') && !executableStyle) {
        reportOverrideWarning('selector 记录没有当前 Runtime 可执行的 text/style 属性；原值保留但不执行', item.selector);
        return;
      }
      parsed.push(item);
    });
    return parsed;
  }
  function styleValueAllowed(key, value) {
    if (typeof value !== 'string' || value.length > 120
        || /[{};]/.test(value) || /url\s*\(|expression\s*\(|javascript:|@import/i.test(value)) return false;
    if (['left','top','width','height','fontSize','letterSpacing','lineHeight','borderRadius'].indexOf(key)>=0) {
      var lengthMatch = value.trim().match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))(?:px|%|em|rem|vh|vw)?$/);
      if (!lengthMatch) return false;
      var magnitude = Number(lengthMatch[1]);
      if (!Number.isFinite(magnitude)) return false;
      if ((key === 'width' || key === 'height') && !(magnitude > 0 && magnitude <= 10000)) return false;
      if (key === 'fontSize' && !(magnitude > 0 && magnitude <= 1000)) return false;
      if ((key === 'left' || key === 'top') && Math.abs(magnitude) > 100000) return false;
      if (key === 'letterSpacing' && Math.abs(magnitude) > 1000) return false;
      if (key === 'lineHeight' && magnitude <= 0) return false;
      return true;
    }
    if (key === 'opacity') return Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= 1;
    if (key === 'position') return ['static','relative','absolute','fixed','sticky'].indexOf(value) >= 0;
    if (key === 'fontWeight') return /^(normal|bold|bolder|lighter|[1-9]00)$/.test(value);
    if (key === 'textAlign') return ['left','right','center','justify','start','end'].indexOf(value) >= 0;
    if (key === 'rotate') return /^-?(?:\d+(?:\.\d+)?|\.\d+)(deg|rad|turn)$/.test(value);
    if (key === 'transform') return /^(none|translate(?:X|Y)?\([-+\d.%a-zA-Z, ]+\)|scale(?:X|Y)?\([\d., -]+\)|rotate\([-+\d.]+deg\))$/.test(value);
    if (key === 'color' || key === 'backgroundColor') {
      return /^(#[0-9a-fA-F]{3,8}|rgba?\([\d.,%\s]+\)|hsla?\([\d.,%\s]+\)|[a-zA-Z]{1,24})$/.test(value);
    }
    return false;
  }
  function applyOverrides() {
    currentOverrides.forEach(function (item) {
      var elements;
      try {
        elements = document.querySelectorAll(item.selector);
      } catch (error) {
        throw new Error('overrides CSS selector 无效：' + item.selector + '；' +
          String(error && error.message || error));
      }
      if (!elements.length) {
        throw new Error('overrides CSS selector 没有匹配到场景元素：' + item.selector);
      }
      elements.forEach(function (element) {
        if (Object.prototype.hasOwnProperty.call(item, 'text')) {
          if (typeof item.text !== 'string') {
            throw new Error('overrides selector ' + item.selector + ' 的 text 不是字符串');
          }
          element.textContent = item.text;
        }
        var style = item.style && typeof item.style === 'object' ? item.style : {};
        Object.keys(style).forEach(function (key) {
          var cssName = overrideStyleMap[key], value = style[key];
          if (!cssName) {
            reportOverrideWarning('style 属性 ' + key + ' 当前 Runtime 不支持；原值保留但不执行', item.selector);
            return;
          }
          if (!styleValueAllowed(key, value)) {
            throw new Error('overrides selector ' + item.selector + ' 的 CSS 属性 ' + key + ' 值无效');
          }
          element.style.setProperty(cssName, value);
        });
      });
    });
  }
  function safeSelectorPart(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, function (char) {
      return '\\' + char.codePointAt(0).toString(16) + ' ';
    });
  }
  function selectorFor(element) {
    if (!element || element.nodeType !== 1) return '';
    if (element.id) return '#' + safeSelectorPart(element.id);
    var parts = [], node = element;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      var part = node.tagName.toLowerCase();
      if (node.id) {
        parts.unshift('#' + safeSelectorPart(node.id));
        break;
      }
      var sibling = node, index = 1;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName === node.tagName) index += 1;
      }
      if (node.parentElement && node.parentElement.children.length > 1) part += ':nth-of-type(' + index + ')';
      parts.unshift(part);
      node = node.parentElement;
      if (node === document.body) {
        parts.unshift('body');
        break;
      }
    }
    return parts.join(' > ').slice(0, 500);
  }
  function reportElement(type, element) {
    if (!element || element === document.body || element === document.documentElement) return;
    var rect = element.getBoundingClientRect(), style = window.getComputedStyle(element);
    var properties = ['color','backgroundColor','fontSize','fontWeight','textAlign','lineHeight',
      'letterSpacing','left','top','width','height','position','opacity','borderRadius','rotate'];
    var styleData = {};
    properties.forEach(function (key) {
      styleData[key] = String(style[key] || '');
    });
    // rect is the painted viewport box and may be scaled/rotated by the preview.
    // localRect is the CSS layout box: Inspector edits must round-trip these design
    // coordinates, not the smaller screen-space rectangle produced by iframe scaling.
    function cssPixel(value, fallback) {
      var text = String(value || '').trim();
      if (!/px$/i.test(text)) return fallback;
      var parsed = Number.parseFloat(text.slice(0, -2));
      return Number.isFinite(parsed) ? parsed : fallback;
    }
    // Inline styles are the persisted design-space values written by
    // applyOverrides(). Prefer them over computed styles, which can be affected
    // by viewport/layout scaling in nested iframe previews.
    var inlineStyle = element.style;
    var localRect = {
      x: cssPixel(inlineStyle.left, cssPixel(style.left, rect.x)),
      y: cssPixel(inlineStyle.top, cssPixel(style.top, rect.y)),
      width: cssPixel(inlineStyle.width, cssPixel(style.width, rect.width)),
      height: cssPixel(inlineStyle.height, cssPixel(style.height, rect.height))
    };
    parent.postMessage({
      type: type,
      selector: selectorFor(element),
      tag: element.tagName.toLowerCase(),
      text: (element.innerText || element.textContent || '').slice(0, 4000),
      textEditable: element.children.length === 0,
      styles: styleData,
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
      localRect: localRect,
      viewport: {width: window.innerWidth, height: window.innerHeight}
    }, '*');
  }
  function elementFromEvent(event) {
    var target = event.target;
    if (!target || target.nodeType !== 1) return null;
    if (target.closest && target.closest('[data-vd-film-bridge]')) return null;
    return target;
  }
  var elementDrag = null;
  function pointerDistance(a, b) {
    return Math.sqrt(Math.pow(a.clientX - b.startX, 2) + Math.pow(a.clientY - b.startY, 2));
  }
  document.addEventListener('pointerdown', function (event) {
    if (!inspectMode) return;
    var element = elementFromEvent(event);
    if (!element) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
    reportElement('film-element-selected', element);
    var rect = element.getBoundingClientRect();
    var nearLeft = Math.abs(event.clientX - rect.left) <= 10;
    var nearRight = Math.abs(event.clientX - rect.right) <= 10;
    var nearTop = Math.abs(event.clientY - rect.top) <= 10;
    var nearBottom = Math.abs(event.clientY - rect.bottom) <= 10;
    var computed = window.getComputedStyle(element);
    var styleLeft = parseFloat(computed.left), styleTop = parseFloat(computed.top);
    // Use editable CSS position rather than painted bounds, which include H1 margins.
    elementDrag = {
      element: element, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
      rect: {x: Number.isFinite(styleLeft) ? styleLeft : rect.x,
        y: Number.isFinite(styleTop) ? styleTop : rect.y,
        width: rect.width, height: rect.height},
      edgeX: nearLeft ? 'left' : (nearRight ? 'right' : ''),
      edgeY: nearTop ? 'top' : (nearBottom ? 'bottom' : ''),
      moved: false,
      original: {position: element.style.position, left: element.style.left, top: element.style.top,
        width: element.style.width, height: element.style.height}
    };
    try { element.setPointerCapture(event.pointerId); } catch (_) {}
  }, true);
  document.addEventListener('pointermove', function (event) {
    if (!inspectMode) return;
    if (elementDrag && event.pointerId === elementDrag.pointerId) {
      var drag = elementDrag;
      if (!drag.moved && pointerDistance(event, drag) < 3) return;
      drag.moved = true;
      event.preventDefault();
      event.stopPropagation();
      if (event.stopImmediatePropagation) event.stopImmediatePropagation();
      var dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
      var next = {
        x: drag.rect.x, y: drag.rect.y,
        width: drag.rect.width, height: drag.rect.height
      };
      if (drag.edgeX === 'left') { next.x += dx; next.width -= dx; }
      else if (drag.edgeX === 'right') next.width += dx;
      else next.x += dx;
      if (drag.edgeY === 'top') { next.y += dy; next.height -= dy; }
      else if (drag.edgeY === 'bottom') next.height += dy;
      else next.y += dy;
      next.width = Math.max(1, next.width);
      next.height = Math.max(1, next.height);
      var element = drag.element;
      // Position fixed is intentionally used for the first-class canvas editing
      // contract: left/top and the selection box share the scene viewport space.
      element.style.position = 'fixed';
      element.style.left = next.x + 'px';
      element.style.top = next.y + 'px';
      element.style.width = next.width + 'px';
      element.style.height = next.height + 'px';
      window.__vdHoveredElement = element;
      reportElement('film-element-preview', element);
      return;
    }
    var element = elementFromEvent(event);
    if (!element) return;
    if (window.__vdHoveredElement === element) return;
    window.__vdHoveredElement = element;
    reportElement('film-element-hover', element);
  }, true);
  document.addEventListener('pointerup', function (event) {
    if (!elementDrag || event.pointerId !== elementDrag.pointerId) return;
    var drag = elementDrag;
    elementDrag = null;
    if (drag.moved) reportElement('film-element-drag-end', drag.element);
  }, true);
  document.addEventListener('pointercancel', function (event) {
    if (!elementDrag || event.pointerId !== elementDrag.pointerId) return;
    var drag = elementDrag;
    elementDrag = null;
    var element = drag.element;
    Object.keys(drag.original).forEach(function (key) { element.style[key] = drag.original[key]; });
    applyOverrides();
  }, true);

  function sendFrameReady(message, supported, error) {
    try {
      parent.postMessage({
        type: 'film-frame:ready',
        frame: message.frame,
        time: message.time,
        supported: supported,
        error: error || null
      }, '*');
    } catch (_) {}
  }
  async function draw(message) {
    var t = Number(message.time);
    if (!Number.isFinite(t) || t < 0) {
      sendFrameReady(message, false, 'frame time must be a finite non-negative number');
      return;
    }
    try {
      await waitUntilLoaded();
      if (!window.film || typeof window.film.frame !== 'function') {
        sendFrameReady(message, false, '页面未实现 window.film.frame(t)');
        return;
      }
      clock.time = t;
      currentOverrides = parseOverrides(message.overrides);
      seed(t);
      clock.queue.length = 0;
      clock.cancelled = Object.create(null);
      clock.active = true;
      syncAnimations(t);
      var result = window.film.frame(t);
      await settleFrameResult(result, t);
      syncAnimations(t);
      flushRaf(t);
      applyOverrides();
      await waitForVisualResources();
      await waitNativeFrame();
      await waitNativeFrame();
      sendFrameReady(message, true, null);
    } catch (error) {
      sendFrameReady(message, true, String(error && error.stack || error));
    } finally {
      clock.active = false;
      clock.queue.length = 0;
      clock.cancelled = Object.create(null);
    }
  }
  // frame(t) mutates the page, so two outstanding asynchronous draws would
  // race on the shared DOM and clock. Exact offline frame requests are serialized;
  // interactive preview seeks are coalesced to the newest requested time.
  var drawChain = Promise.resolve();
  var pendingPreview = null;
  var previewScheduled = false;

  function enqueueExactFrame(message) {
    drawChain = drawChain.then(function () { return draw(message); }).catch(function (error) {
      sendFrameReady(message, true, String(error && error.stack || error));
    });
  }
  function enqueuePreviewTime(message) {
    pendingPreview = message;
    if (previewScheduled) return;
    previewScheduled = true;
    drawChain = drawChain.then(async function () {
      while (pendingPreview) {
        var next = pendingPreview;
        pendingPreview = null;
        await draw({
          type: 'film-frame',
          time: next.time,
          frame: -1,
          overrides: next.overrides
        });
      }
      previewScheduled = false;
    }).catch(function (error) {
      previewScheduled = false;
      pendingPreview = null;
      try { parent.postMessage({type:'film-preview:error', error:String(error)}, '*'); } catch (_) {}
    });
  }

  window.addEventListener('message', function (event) {
    if (event.source !== parent || !event.data) return;
    var message = event.data;
    if (message.type === 'film-frame') enqueueExactFrame(message);
    else if (message.type === 'film-time' && window.film && typeof window.film.frame === 'function') {
      enqueuePreviewTime(message);
    } else if (message.type === 'film-overrides') {
      currentOverrides = parseOverrides(message.overrides);
      applyOverrides();
    } else if (message.type === 'film-inspect') {
      // Inspection-ready is a rendering barrier, not just a pointer toggle.
      // A preceding film-time can still be applying persisted overrides. Queue
      // the acknowledgement after that draw so a fast post-reload click cannot
      // inspect stale pre-override coordinates.
      var inspectEnabled = !!message.enabled;
      drawChain = drawChain.then(function () {
        inspectMode = inspectEnabled;
        if (!inspectMode) window.__vdHoveredElement = null;
        else parent.postMessage({type:'film-inspect-ready',enabled:true}, '*');
      }).catch(function (error) {
        parent.postMessage({type:'film-inspect-ready',enabled:false,error:String(error && error.message || error)}, '*');
      });
    } else if (message.type === 'film-element-edit') {
      var incoming = message.override;
      if (!incoming || typeof incoming.selector !== 'string') return;
      var found = currentOverrides.find(function (item) { return item.selector === incoming.selector; });
      if (found) {
        if (Object.prototype.hasOwnProperty.call(incoming, 'text')) found.text = incoming.text;
        found.style = Object.assign({}, found.style || {}, incoming.style || {});
      } else currentOverrides.push(incoming);
      applyOverrides();
    }
  });
})();
"""


_ATTR_RE = re.compile(r"\b(src|href|poster|data-src)\s*=\s*(['\"])(.*?)\2", re.I | re.S)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_STYLE_ATTR_RE = re.compile(r"\bstyle\s*=\s*(['\"])(.*?)\1", re.I | re.S)
_CSS_IMPORT_RE = re.compile(r"(@import\s+)(?:url\(\s*)?(['\"])(.*?)\2\s*\)?", re.I | re.S)


def _asset_url(value: str, project_id: str, page_src: str, route: str) -> str | None:
    value = html.unescape(value.strip())
    if not value or value.startswith(("#", "\\", "//")):
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    page_path = unquote(page_src.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    source_path = unquote(parts.path)
    # A root-relative scene URL is still project-local. Resolve it from the
    # project root instead of letting it fall through to the WebUI origin.
    if source_path.startswith("/"):
        joined = posixpath.normpath(source_path.lstrip("/"))
    else:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), source_path))
    if joined in ("", ".") or joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    url = route + "?project_id=" + quote(project_id, safe="") + "&src=" + quote(joined, safe="")
    if parts.query:
        url += "&resource_query=" + quote(parts.query, safe="")
    if parts.fragment:
        url += "#" + parts.fragment
    return url


def rewrite_scene_css(source: str, project_id: str, src: str, route: str) -> str:
    def replace_url(match: re.Match) -> str:
        target = _asset_url(match.group(2), project_id, src, route)
        if target is None:
            return match.group(0)
        return "url('" + target.replace("'", "%27") + "')"

    def replace_import(match: re.Match) -> str:
        target = _asset_url(match.group(3), project_id, src, route)
        if target is None:
            return match.group(0)
        return match.group(1) + "url('" + target.replace("'", "%27") + "')"

    result = _CSS_URL_RE.sub(replace_url, source)
    return _CSS_IMPORT_RE.sub(replace_import, result)


def inject_scene_runtime(source: str, project_id: str, src: str,
                         route: str = "/api/editor/film/media") -> str:
    """Resolve relative HTML resources and install the deterministic scene bridge."""
    def replace_attr(match: re.Match) -> str:
        target = _asset_url(match.group(3), project_id, src, route)
        if target is None:
            return match.group(0)
        return match.group(1) + "=" + match.group(2) + html.escape(target, quote=True) + match.group(2)

    result = _ATTR_RE.sub(replace_attr, source)
    def replace_style_attr(match: re.Match) -> str:
        rewritten = rewrite_scene_css(html.unescape(match.group(2)), project_id, src, route)
        return "style=" + match.group(1) + html.escape(rewritten, quote=True) + match.group(1)

    result = _STYLE_ATTR_RE.sub(replace_style_attr, result)


    def rewrite_style(match: re.Match) -> str:
        return match.group(1) + rewrite_scene_css(match.group(2), project_id, src, route) + match.group(3)

    result = re.sub(r"(<style\b[^>]*>)(.*?)(</style\s*>)", rewrite_style, result, flags=re.I | re.S)

    bridge = "<script data-vd-film-bridge=\"1\">" + _SCENE_BRIDGE + "</script>"
    head = re.search(r"<head\b[^>]*>", result, flags=re.I)
    if head:
        result = result[:head.end()] + bridge + result[head.end():]
    else:
        html_open = re.search(r"<html\b[^>]*>", result, flags=re.I)
        result = result[:html_open.end()] + bridge + result[html_open.end():] if html_open else bridge + result
    return result


def inject_scene_css(source: str, project_id: str, src: str,
                     route: str = "/api/editor/film/media") -> str:
    return rewrite_scene_css(source, project_id, src, route)
