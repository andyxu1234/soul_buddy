/**
 * 站点 mermaid 渲染（配合 mkdocs.yml 里 class: mermaid-src 的 superfences 栅栏）。
 *
 * 为什么不用 Material 内置的 mermaid 集成：它的加载器固定从 unpkg.com 拉
 * mermaid@11，该 CDN 在国内网络基本不可达（请求挂起 → 图全部空白）。
 *
 * 为什么用 mermaid.render 而不是 mermaid.run：mermaid.run 读取节点的
 * innerHTML，superfences 产出的 <pre class="mermaid-src"><code>…</code></pre>
 * 会把 <code> 标签和实体一起带进图源码，导致部分图报 "Syntax error in text"。
 * 这里自己取 textContent（纯文本源码）逐个 render，行为与探测脚本一致。
 *
 * 单图失败只在控制台告警，不影响其余图；没有图的页面零脚本加载。
 */
(function () {
  var nodes = Array.prototype.slice.call(
    document.querySelectorAll("pre.mermaid-src > code")
  );
  if (!nodes.length) return;

  var s = document.createElement("script");
  s.src = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js";
  s.onload = function () {
    mermaid.initialize({
      startOnLoad: false,
      theme: "base",
      themeVariables: { fontFamily: "inherit" },
      flowchart: { useMaxWidth: true, htmlLabels: true },
      sequence: { useMaxWidth: true },
      state: { useMaxWidth: true }
    });
    nodes.forEach(function (code, idx) {
      var src = (code.textContent || "").trim();
      if (!src) return;
      mermaid.render("mmd-" + idx + "-" + Date.now(), src).then(function (r) {
        var pre = code.parentElement;
        var holder = document.createElement("div");
        holder.className = "mermaid-svg";
        holder.innerHTML = r.svg;
        pre.replaceWith(holder);
      }).catch(function (e) {
        // 渲染失败保留源码可见，方便定位
        if (window.console) console.warn("mermaid render failed:", e);
      });
    });
  };
  document.head.appendChild(s);
})();
