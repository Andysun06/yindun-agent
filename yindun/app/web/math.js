/* ==========================================================================
   隐盾 · 离线公式渲染（KaTeX）
   为什么单独一层：KaTeX 只负责 TeX → HTML，Markdown 的转义/占位顺序仍由 app.js 控制。
   调用方保证：代码块与行内代码已经先占位，因此代码里的 $ / \( 不会被误渲染。
   安全：KaTeX 以 trust:false + throwOnError:false 运行，输出只含它自己生成的白名单标签。
   ========================================================================== */
(() => {
  "use strict";

  // $$...$$ / \[...\] 为块级；\(...\) / $...$ 为行内。
  const MATH_RE = /(\$\$([\s\S]+?)\$\$)|(\\\[([\s\S]+?)\\\])|(\\\((.+?)\\\))|(\$([^$\n]+?)\$)/g;
  const ENTITIES = { "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&amp;": "&" };

  function unescapeMath(text) {
    // esc() 先把 < > & 转成了实体；TeX 需要原始字符。单次替换，避免 &amp;lt; 二次解码。
    return String(text || "").replace(/&(lt|gt|quot|#39|amp);/g, (m) => ENTITIES[m] || m);
  }

  function renderEscaped(text, stash) {
    if (!window.katex || !text) return text;
    return text.replace(MATH_RE, (whole, _d0, dtex, _b0, btex, _i0, itex, _s0, stex) => {
      const display = Boolean(dtex || btex);
      const tex = String(dtex || btex || itex || stex || "").trim();
      if (!tex) return whole;
      if (!display && stex !== undefined) {
        // 货币/普通美元符号的误判防线：开闭 $ 必须紧贴内容，且内容要有数学信号。
        if (!/^\S(?:.*\S)?$/.test(stex)) return whole;
        const hasSignal = (/[\\^_{}=<>+\-*/(),]/.test(tex) && !/^[\d.,]+$/.test(tex))
          || /^[A-Za-z]$/.test(tex);
        if (!hasSignal) return whole;
      }
      try {
        return stash(window.katex.renderToString(unescapeMath(tex), {
          displayMode: display,
          throwOnError: false,
          trust: false,
          strict: "ignore",
        }));
      } catch (e) {
        return whole;   // 渲染失败就保留原文，绝不吞掉用户内容
      }
    });
  }

  window.YindunMath = { renderEscaped };
})();
