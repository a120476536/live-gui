// ==UserScript==
// @name         抖音直播 DOM 双监控【调试版】
// @namespace    http://tampermonkey.net/
// @version      2025.06.19-debug
// @description  带完整日志，方便排查「抓不到」的原因
// @match        *://live.douyin.com/*
// @grant        none
// ==/UserScript==
(() => {
    'use strict';
  
    /* ---------- 0. 日志开关 ---------- */
    const log = (...a) => console.log('[DEBUG]', ...a);
  
    /* ---------- 1. 去重先关掉 ---------- */
    const pushUnique = () => true;   // 全部放行
  
    /* ---------- 2. 解析器 ---------- */
    function parseChat(node) {
      const full = node.textContent.trim();
      log('【聊天原始文本】', full);
  
      const giftMatch = full.match(/^(.*?)：.*?送出了(.*?)\s*×\s*(\d+)$/);
      if (giftMatch) {
        const [, nick, giftName, cnt] = giftMatch;
        return { type: 'gift', source: '聊天', user: nick, gift: giftName.trim(), count: cnt };
      }
  
      const dmMatch = full.match(/^(.*?)：(.+)$/);
      if (dmMatch) {
        const [, nick, msg] = dmMatch;
        return { type: 'danmu', source: '聊天', user: nick, gift: msg };
      }
      return null;
    }
  
    function parseDanmu(danmu) {
      log('【弹幕节点】', danmu);
  
      // 礼物
      const img = danmu.querySelector('img[src*="webcast/"][src$=".png"]');
      if (img) {
        const spans = [...img.parentElement.children]
          .filter(el => el.tagName === 'SPAN')
          .map(el => el.textContent.trim());
        log('【礼物 spans】', spans);
        const user  = spans[0] || '';
        const gift  = spans.slice(1, -2).join(' ');
        const count = spans.at(-1) || '1';
        return { type: 'gift', source: '弹幕', user, gift, count };
      }
  
      // 普通弹幕
      const txt = danmu.querySelector('span[class*="content-with-emoji-text"]');
      if (txt) {
        return { type: 'danmu', source: '弹幕', user: '', gift: txt.textContent.trim() };
      }
      return null;
    }
  
    /* ---------- 3. 监听聊天行 ---------- */
    function watchChat() {
      const ob = new MutationObserver(list => {
        list.forEach(m => m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          const match = n.matches?.('[data-index]');
          log('【聊天行新增】匹配?', match, n);
          if (!match) return;
  
          const res = parseChat(n);
          if (res) log('【聊天结果】', res);
        }));
      });
      ob.observe(document.body, { childList: true, subtree: true });
      log('[聊天] 监听已挂');
    }
  
    /* ---------- 4. 监听水平弹幕 ---------- */
    /* ---------- 4. 监听水平弹幕（修复后） ---------- */
  function watchDanmu() {
    const root = document.getElementById('DanmakuLayout');
    if (!root) {
      log('[弹幕] 未找到 #DanmakuLayout，1 秒后重试');
      setTimeout(watchDanmu, 1000);
      return;
    }
  
    const ob = new MutationObserver(list => {
      list.forEach(m => m.addedNodes.forEach(n => {
        if (n.nodeType !== 1) return;
        // 这里既可以监听 .danmu 本身，也可以监听更深层
        const res = parseDanmu(n) || (n.querySelector && parseDanmu(n.querySelector('.danmu')));
        if (res) log('【弹幕结果】', res);
      }));
    });
    ob.observe(root, { childList: true, subtree: true }); // ✅ 关键修复
    log('[弹幕] 监听已挂 →', root);
  }
  
    /* ---------- 5. 启动 ---------- */
    document.readyState === 'loading'
      ? document.addEventListener('DOMContentLoaded', () => { watchChat(); watchDanmu(); })
      : (watchChat(), watchDanmu());
  })();