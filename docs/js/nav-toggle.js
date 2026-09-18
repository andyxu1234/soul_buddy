/* ============================================================================
   左侧目录：折叠 / 展开
   ----------------------------------------------------------------------------
   按钮本体由 docs/hooks/nav_toggle_hook.py 在构建时注入 header，
   首帧防闪跳的内联脚本也在那儿。这里只负责「交互 + 状态持久化」。

   状态落在 <html> 上的 `sb-nav-collapsed` 类，而不是给按钮自己加类：
   侧栏是 <html> 的后代（.md-sidebar--primary），把状态放在根节点上，
   CSS 选择器最短，也最不容易被 Material 的样式盖掉。
   ========================================================================== */
(function () {
  "use strict";

  var KEY = "sb.nav";
  var ROOT = document.documentElement;
  var CLS = "sb-nav-collapsed";
  var MOBILE = "(max-width: 59.984375em)";

  function isCollapsed() {
    return ROOT.classList.contains(CLS);
  }

  function readStored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      /* 隐私模式下 localStorage 可能直接抛异常 */
      return null;
    }
  }

  /* 同步按钮的无障碍状态，并让隐藏 checkbox 跟着走 */
  function sync() {
    var collapsed = isCollapsed();
    var btn = document.querySelector(".sb-nav-toggle");
    if (!btn) return;
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.setAttribute(
      "title",
      (collapsed ? "展开目录" : "收起目录") + "（快捷键 \\）"
    );
    var box = btn.querySelector(".sb-nav-toggle__state");
    if (box) box.checked = collapsed;
  }

  function set(collapsed, persist) {
    ROOT.classList.toggle(CLS, collapsed);
    if (persist) {
      try {
        localStorage.setItem(KEY, collapsed ? "collapsed" : "expanded");
      } catch (e) {
        /* 存不下也没关系，本次会话内交互照常工作 */
      }
    }
    sync();
  }

  function toggle() {
    set(!isCollapsed(), true);
  }

  /* 事件委托挂在 document 上：Material 若启用 instant loading 会换掉 DOM */
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || typeof t.closest !== "function") return;
    if (!t.closest(".sb-nav-toggle")) return;
    ev.preventDefault();
    ev.stopPropagation();
    toggle();
  });

  /* 键盘：反斜杠切换，和多数 IDE 的侧栏快捷键一致 */
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "\\" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    var el = ev.target;
    var tag = el && el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (el && el.isContentEditable)) {
      return;
    }
    ev.preventDefault();
    toggle();
  });

  /* 跨过 60em 断点时，常驻栏与抽屉会互换：回到抽屉区间就清掉折叠状态，
     否则切回宽屏时侧栏会「莫名其妙不见了」。 */
  var mq = window.matchMedia(MOBILE);
  function onBreak(ev) {
    if (ev.matches) ROOT.classList.remove(CLS);
    else if (readStored() === "collapsed") ROOT.classList.add(CLS);
    sync();
  }

  if (mq.addEventListener) mq.addEventListener("change", onBreak);
  else if (mq.addListener) mq.addListener(onBreak);

  sync();
})();
