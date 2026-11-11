// ==UserScript==
// @name         快手+抖音直播消息融合版
// @namespace    http://tampermonkey.net/
// @version      1.0.0
// @description  单脚本抓取快手/抖音直播消息，快手用WS，抖音用HTTP 融合均用HTTP
// @match        *://live.kuaishou.com/*
// @match        *://*.kuaishou.com/*
// @match        *://live.douyin.com/*
// @match        *://*.douyin.com/*
// @connect      localhost
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @run-at       document-start
// ==/UserScript==

(() => {
    'use strict';
    const log = (...a) => console.log('%c[融合脚本]', 'color:#009688;font-weight:bold', ...a);

    /******************************************************************
     * 公共工具
     ******************************************************************/
    function deepRoot(el) {
      while (el && el.shadowRoot) el = el.shadowRoot;
      return el;
    }

    /******************************************************************
     * 快手逻辑（WebSocket）
     ******************************************************************/
    const KS_HTTP_URL ="http://127.0.0.1:8766";
    const ks_sendQueue = [];
    let ks_sending = false;
    function ks_queueSend(data) {
      ks_sendQueue.push(data);
      if (!ks_sending) ks_startQueueSend();
    }
    function ks_startQueueSend() {
      ks_sending = true;
      const timer = setInterval(() => {
        if (ks_sendQueue.length === 0) {
          clearInterval(timer);
          ks_sending = false;
          return;
        }
        const item = ks_sendQueue.shift();
        ks_sendHttp(item);
      }, 100);
    }
    function ks_sendHttp(data) {
      GM_xmlhttpRequest({
      method: "POST",
      url: KS_HTTP_URL,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify(data),
      onload: res => log('[快手][HTTP] 已发送 →', res.responseText),
      onerror: err => log('[快手][HTTP] 发送失败 →', err)
    });
  }


    function ks_extract(node) {
      if (!node || !node.querySelector) return null;
      const nick = node.querySelector('.username')?.textContent?.trim();
      const msg = node.querySelector('.comment')?.textContent?.trim();
      const imgEl = node.querySelector('.gift-img');
      const imgUrl = imgEl?.src || imgEl?.getAttribute('src');
      const likeE1 = node.querySelector('.like');

      let jsonChat = {};
      if (imgUrl) {
        jsonChat = { from: "[快手][观众]", source: "聊天", nickName: nick, giftName: msg, giftCount: 1, giftImg: imgUrl };
      } else if (likeE1) {
        jsonChat = { from: "[快手][观众]", source: "聊天", nickName: nick, giftName: "点亮❤️" };
      } else {
        jsonChat = { from: "[快手][观众]", source: "聊天", nickName: nick, msg: msg };
      }
      return jsonChat;
    }

    function ks_listenGiftPanel() {
      const ob = new MutationObserver(ms => {
        ms.forEach(m => m.addedNodes.forEach(n => {
          if (!n || !n.querySelector) return;
          const g = n.closest('.gift-slot-item') || n;
          const nick = g.querySelector('.gift-info h4')?.textContent?.trim();
          const giftName = g.querySelector('.gift-name')?.textContent?.trim();
          const giftImg = g.querySelector('.gift-img img')?.src;
          const giftCount = g.querySelector('.gift-combo span')?.textContent?.trim() || '1';
          if (!nick || !giftName) return;
          let jsonGift = { from: "[快手][观众]", source: "礼物", nickName: nick, giftName: giftName, giftCount: giftCount, giftImg: giftImg };
          ks_queueSend(jsonGift);
        }));
      });
      ob.observe(document.body, { childList: true, subtree: true });
    }

    function ks_listenChatList(listNode) {
      let innerOb = new MutationObserver(ms => {
        ms.forEach(m => m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          const txt = ks_extract(n);
          if (txt) {
            log('[快手][聊天]', txt);
            //ks_sendSafe(txt);
              // ks_sendHttp(jsonGift);
              ks_queueSend(res);
          }
        }));
      });
      innerOb.observe(listNode, { childList: true, subtree: true });
    }

    function ks_poll() {
      const root = deepRoot(document.body);
      const list = root.querySelector('.chat-history .wrapper') || root.querySelector('.chat-list') || root.querySelector('[class*="chat"] [class*="list"]');
      if (!list) { requestIdleCallback(ks_poll); return; }
      ks_listenChatList(list);
      ks_listenGiftPanel();
    }

    /******************************************************************
     * 抖音逻辑（HTTP）
     ******************************************************************/
    const DY_HTTP_URL = "http://127.0.0.1:8766";
    const dy_sendQueue = [];
    let dy_sending = false;
    function dy_queueSend(data) {
      dy_sendQueue.push(data);
      if (!dy_sending) dy_startQueueSend();
    }
    function dy_startQueueSend() {
      dy_sending = true;
      const timer = setInterval(() => {
        if (dy_sendQueue.length === 0) {
          clearInterval(timer);
          dy_sending = false;
          return;
        }
        const item = dy_sendQueue.shift();
        dy_sendHttp(item);
      }, 100);
    }
    function dy_sendHttp(data) {
      GM_xmlhttpRequest({
        method: "POST",
        url: DY_HTTP_URL,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify(data),
        onload: res => log('[抖音][HTTP] 已发送 →', res.responseText),
        onerror: err => log('[抖音][HTTP] 发送失败 →', err)
      });
    }

    function dy_parseChat(node) {
      const full = node.textContent.trim();
      if (full.includes(':')) {
        let [nickName, content] = full.split(':');
        if (nickName && content) return { from: "[抖音][观众]", source: "聊天", nickName, msg: content };
      } else if (full) {
        return { from: "[抖音][观众]", source: "聊天", nickName: full, msg: full };
      }
      return null;
    }
    function dy_parseDanmu(danmu) {
      const img = danmu.querySelector('img[src*="webcast/"][src$=".png"]');
      if (img) {
        const spans = [...img.parentElement.children].filter(el => el.tagName === 'SPAN').map(el => el.textContent.trim());
        let dataUserGif = spans[0] || '';
        let nickName = dataUserGif, giftName = dataUserGif;
        if (/\s/.test(dataUserGif)) {
          const parts = dataUserGif.trim().split(/\s+/);
          nickName = parts[0]; giftName = parts[1];
        }
        const count = spans.at(-1) || '1';
        return { from: "[抖音][观众]", source: "礼物", nickName, giftName, count, gifImg: img.src };
      }
      const txt = danmu.querySelector('span[class*="content-with-emoji-text"]');
      if (txt) return { from: "[抖音][观众]", source: "弹幕聊天", nickName: "", msg: txt.textContent.trim() };
      return null;
    }
    function dy_watchChat() {
      const ob = new MutationObserver(list => {
        list.forEach(m => m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          if (!n.matches?.('[data-index]')) return;
          const res = dy_parseChat(n);
          if (res) { log('[抖音][聊天]', res); dy_queueSend(res); }
        }));
      });
      ob.observe(document.body, { childList: true, subtree: true });
    }
    function dy_watchDanmu() {
      const root = document.getElementById('DanmakuLayout');
      if (!root) { setTimeout(dy_watchDanmu, 1000); return; }
      const ob = new MutationObserver(list => {
        list.forEach(m => m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          const res = dy_parseDanmu(n) || (n.querySelector && dy_parseDanmu(n.querySelector('.danmu')));
          if (res) { log('[抖音][弹幕]', res); dy_queueSend(res); }
        }));
      });
      ob.observe(root, { childList: true, subtree: true });
    }

    /******************************************************************
     * 启动
     ******************************************************************/
    if (location.host.includes('kuaishou.com')) {
      log('进入快手直播页 → 启动快手逻辑');
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            //ks_openWS();
                                                             requestIdleCallback(ks_poll); });
      } else {
        //ks_openWS();
          requestIdleCallback(ks_poll);
      }
    } else if (location.host.includes('douyin.com')) {
      log('进入抖音直播页 → 启动抖音逻辑');
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { dy_watchChat(); dy_watchDanmu(); });
      } else {
        dy_watchChat(); dy_watchDanmu();
      }
    }
  })();
