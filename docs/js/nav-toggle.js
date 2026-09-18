/* ============================================================================
   目录折叠 / 展开 —— 左侧导航 + 右侧本页目录
   ----------------------------------------------------------------------------
   按钮本体由 docs/hooks/nav_toggle_hook.py 在构建时注入，
   首帧防闪跳的内联脚本也在那儿。这里只负责「交互 + 状态持久化」。

   状态落在 <html> 上的两个类，而不是给按钮自己加类：
   两个侧栏都是 <html> 的后代，把状态放在根节点上，CSS 选择器最短，
   也最不容易被 Material 的样式盖掉。

   两侧共用一套逻辑（CONFIGS），差别只有 类名 / 存储键 / 选择器 / 提示语。
   ========================================================================== */
(function () {
  "use strict";

  var ROOT = document.documentElement;
  var MOBILE = "(max-width: 59.984375em)";

  var CONFIGS = [
    {
      cls: "sb-nav-collapsed",
      key: "sb.nav",
      sel: ".sb-nav-toggle",
      what: "目录",
      keyHint: "\\",
    },
    {
      cls: "sb-toc-collapsed",
      key: "sb.toc",
      sel: ".sb-toc-toggle",
      what: "本页目录",
      keyHint: "Shift+\\",
    },
  ];

  function readStored(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      /* 隐私模式下 localStorage 可能直接抛异常 */
      return null;
    }
  }

  function writeStored(key, collapsed) {
    try {
      localStorage.setItem(key, collapsed ? "collapsed" : "expanded");
    } catch (e) {
      /* 存不下也没关系，本次会话内交互照常工作 */
    }
  }

  function isCollapsed(cfg) {
    return ROOT.classList.contains(cfg.cls);
  }

  /* 同步按钮的无障碍状态，并让隐藏 checkbox 跟着走 */
  function syncOne(cfg) {
    var btn = document.querySelector(cfg.sel);
    if (!btn) return;
    var collapsed = isCollapsed(cfg);
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.setAttribute(
      "title",
      (collapsed ? "展开" : "收起") + cfg.what + "（快捷键 " + cfg.keyHint + "）"
    );
    var box = btn.querySelector("input");
    if (box) box.checked = collapsed;
  }

  function syncAll() {
    CONFIGS.forEach(syncOne);
  }

  function set(cfg, collapsed, persist) {
    ROOT.classList.toggle(cfg.cls, collapsed);
    if (persist) writeStored(cfg.key, collapsed);
    syncOne(cfg);
  }

  function toggle(cfg) {
    set(cfg, !isCollapsed(cfg), true);
  }

  /* 事件委托挂在 document 上：Material 若启用 instant loading 会换掉 DOM */
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || typeof t.closest !== "function") return;
    for (var i = 0; i < CONFIGS.length; i++) {
      if (t.closest(CONFIGS[i].sel)) {
        ev.preventDefault();
        ev.stopPropagation();
        toggle(CONFIGS[i]);
        return;
      }
    }
  });

  /* 键盘：反斜杠切换左栏（和多数 IDE 的侧栏快捷键一致），
     Shift+反斜杠 切换右侧本页目录。 */
  document.addEventListener("keydown", function (ev) {
    var which = null;
    if (ev.key === "\\" && !ev.shiftKey) which = CONFIGS[0];
    else if (ev.key === "|") which = CONFIGS[1];  /* Shift+\ 在多数布局上产出 | */
    if (!which) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    var el = ev.target;
    var tag = el && el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (el && el.isContentEditable)) {
      return;
    }
    ev.preventDefault();
    toggle(which);
  });

  /* 跨过 60em 断点时，常驻栏与抽屉会互换：回到抽屉区间就清掉折叠状态，
     否则切回宽屏时侧栏会「莫名其妙不见了」。右侧同理（窄屏本就不显示）。 */
  var mq = window.matchMedia(MOBILE);
  function onBreak(ev) {
    CONFIGS.forEach(function (cfg) {
      if (ev.matches) ROOT.classList.remove(cfg.cls);
      else if (readStored(cfg.key) === "collapsed") ROOT.classList.add(cfg.cls);
    });
    syncAll();
  }

  if (mq.addEventListener) mq.addEventListener("change", onBreak);
  else if (mq.addListener) mq.addListener(onBreak);

  syncAll();
})();
