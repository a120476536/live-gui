// ==UserScript==
// @name         抖音直播礼物浮层监听
// @namespace    http://tampermonkey.net/
// @version      0.2
// @description  监听抖音直播送礼物浮层DOM变化并输出，同时抓取用户名
// @match        *://live.douyin.com/*
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  console.log('%c[抖音礼物监听脚本已启动]', 'color:#ff0050;font-weight:bold');

  // 工具：递归展开 shadowRoot
  function deepRoot(el) {
    while (el && el.shadowRoot) el = el.shadowRoot;
    return el;
  }

  // 获取用户名
  function getUsername(el) {
    // 假设用户名在礼物元素的上一个或下一个兄弟节点中
    // 或者在同一个父元素中
    const parent = el.parentElement;
    if (!parent) return null;

    // 尝试查找用户名
    const username = parent.querySelector('[class*="username"], [class*="user-name"], [class*="nick"]');
    if (username) {
      return username.textContent?.trim();
    }

    // 如果没有找到，尝试查找兄弟节点
    const prevSibling = el.previousElementSibling;
    const nextSibling = el.nextElementSibling;

    if (prevSibling && /username|user-name|nick/i.test(prevSibling.className)) {
      return prevSibling.textContent?.trim();
    }

    if (nextSibling && /username|user-name|nick/i.test(nextSibling.className)) {
      return nextSibling.textContent?.trim();
    }

    return null;
  }

  // 监听函数
  function watchGifts() {
    const target = document.body;
    if (!target) return;

    const ob = new MutationObserver(ms => {
      ms.forEach(m => {
        m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;

          const el = deepRoot(n);

          // 简单判断：类名里带 gift、礼物的节点
          if (el.className && /gift|礼物/i.test(el.className)) {
            const username = getUsername(el);
            console.log('[礼物浮层]', el, el.textContent?.trim(), '[用户名]', username);
          }

          // 深挖子节点
          const gifts = el.querySelectorAll?.('[class*="gift"],[class*="礼物"]');
          if (gifts && gifts.length) {
            gifts.forEach(g => {
              const username = getUsername(g);
              console.log('[礼物子节点]', g, g.textContent?.trim(), '[用户名]', username);
            });
          }
        });
      });
    });

    ob.observe(target, { childList: true, subtree: true });
    console.log('[监听已开启] 正在等待礼物浮层出现...');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', watchGifts);
  } else {
    watchGifts();
  }
})();