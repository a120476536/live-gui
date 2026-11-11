// ==UserScript==
// @name         快手直播消息-单连接版（可见页面保活）
// @version      2.0
// @description  稳定抓取快手直播消息，单连接、断线缓存、切页保活
// @match        *://live.kuaishou.com/*
// @match        *://*.kuaishou.com/*
// @grant        none
// @run-at       document-start
// ==/UserScript==

(() => {
  'use strict';
  console.log('%c[TM] 快手脚本已跑（v2 单连接版）', 'color:#00c853;font-weight:bold');

  /* ---------- WebSocket 管理 ---------- */
  // 全项目唯一对外 WS 端口（8767 已彻底停用，不要再写第二个端口）
  const WS_URL = 'ws://localhost:8765';
  let ws = null;
  let wsOpen = false;
  let connectTime = 0; // 记录连接建立时间
  let reconnectTimer = null;
  let throwAwayTime = 3000; // 新连接 丢弃消息
  let backoff = 1000;                 // 首次重连 1s，最大 30s
  const MAX_BACKOFF = 30_000;
  const queue = [];                   // 断线消息缓存

  /* 真正建立连接 */
  function openWS() {
    if (ws) return;                   // 保证最多一条
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      console.log('[WS] 已连接');
      wsOpen = true;
      connectTime = Date.now(); // 记录连接时间
      backoff = 1000;                 // 成功后重置退避
      flush();                        // 把离线消息发出去
      window.__ws = ws;
    };
    ws.onclose = () => {
      console.log('[WS] 断开，准备重连');
      ws = null;
      wsOpen = false;
      scheduleReconnect();
    };
    ws.onerror = e => console.error('[WS] 连接错误', e);
  }

  /* 指数退避重连 */
  function scheduleReconnect() {
    if (document.hidden) return;      // 页面隐藏不重连
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      if (!ws) openWS();
    }, backoff);
    backoff = Math.min(backoff * 2, MAX_BACKOFF);
  }

  /* 发消息，失败就缓存 */
  function sendSafe(payload) {
     if (wsOpen && Date.now() - connectTime < throwAwayTime && payload.startsWith('[观众]')) {
          console.log('[丢弃] '+(throwAwayTime/1000)+'s 内消息:'+payload);
       return;
    }
    if (wsOpen) {
      ws.send(JSON.stringify(payload));
    } else {
      queue.push(payload);
    }
  }
  function flush() {
    while (queue.length && wsOpen) {
      ws.send(JSON.stringify(queue.shift()));
    }
  }

  /* 页面可见性管理 */
  function handleVisibility() {
    if (document.hidden) {
      // 隐藏时立即关闭，防止后台心跳
      /* if (ws) {
        ws.close();
        ws = null;
        wsOpen = false;
      }*/
      clearTimeout(reconnectTimer);
    } else {
      // 重新可见时立刻连
      openWS();
    }
  }
  document.addEventListener('visibilitychange', handleVisibility);

  /* 页面加载完就第一次连接 */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', openWS);
  } else {
    openWS();
  }


  /* ---------- 以下为你的原始逻辑，仅把 sendWS 换成 sendSafe ---------- */
  const seen = new Set();
//   const _fetch = window.fetch;
//   window.fetch = function(resource, init) {
//     const url = typeof resource === 'string' ? resource : resource.url;

//     if (url && url.includes('/live_api/emoji/gift-list')) {
//       console.log('[快手][拦截全礼物] fetch ->', url);
//       try {
//         sendSafe(`[快手][拦截全礼物]#@#${url}`);
//       } catch (e) {
//         console.error('sendSafe error:', e);
//       }
//     }

//     // ✅ 参数必须完整透传，不能用 arguments
//     return _fetch.call(this, resource, init);
//   };

// /* 2. XHR 拦截（快手实际用的就是它） */
//    const _open = XMLHttpRequest.prototype.open;
//     XMLHttpRequest.prototype.open = function (method, url) {
//         if (url.includes('/live_api/emoji/gift-list')) {
//             console.log('[快手][拦截全礼物] XHR  ->', url);
//             let key = `[快手][拦截全礼物]#@#${url}`;
//             sendSafe(key)
//         }
//         return _open.apply(this, arguments);
//     };
  /* 递归找最深的 ShadowRoot */
  function deepRoot(el) {
    while (el && el.shadowRoot) el = el.shadowRoot;
    return el;
  }

  /* 提取昵称:内容 */
  function extract(node) {
    if (!node || !node.querySelector) return null;

    const nick = node.querySelector('.username')?.textContent?.trim();
    const msg  = node.querySelector('.comment')?.textContent?.trim();
    const imgEl = node.querySelector('.gift-img');
    const imgUrl = imgEl?.src || imgEl?.getAttribute('src');
    const likeE1 = node.querySelector('.like');

    // let key;
    // "source":"聊天","data":txt
    // let jsonGift = {"from":"[快手][观众]","source":"礼物","nickName":nick,"giftName":giftName,"giftCount":giftCount,"giftImg":giftImg}
    let jsonChat = {}
    if (imgUrl) {
      // key = `[礼物]#@#${nick}#@#送出#@#${msg}#@#x1#@#${imgUrl}`;
      giftCount = 1
      jsonChat =  {"from":"[快手][观众]","source":"聊天","nickName":nick,"giftName":msg,"giftCount":giftCount,"giftImg":imgUrl}
    } else if (likeE1) {
      // key = `[点亮]#@#${nick}#@#点亮❤️`;
      jsonChat =  {"from":"[快手][观众]","source":"聊天","nickName":nick,"giftName":"点亮❤️"}
    } else {
      // key = `[弹幕]#@#${nick}#@#${msg}`;
      jsonChat =  {"from":"[快手][观众]","source":"聊天","nickName":nick,"msg":msg}
    }

    return seen.has(jsonChat) ? null : (seen.add(jsonChat), jsonChat);
  }

  /* 监听礼物浮层 --快手单独*/
  // function listenGiftPanel() {
  //   const ob = new MutationObserver(ms => {
  //     ms.forEach(m => m.addedNodes.forEach(n => {
  //       if (!n || !n.querySelector) return;

  //       const g = n.closest?.('.gift-stack') || n.closest?.('.gift-panel') || n;
  //       const nick = g.querySelector('.gift-info h4, .username')?.textContent?.trim();
  //       const giftName = g.querySelector('.gift-name')?.textContent?.trim();
  //       const giftImg  = g.querySelector('.gift-img')?.src;
  //       const giftCount = g.querySelector('.gift-combo, .gift-count')?.textContent?.trim() || '1';
  //       if (!nick || !gift) return;

  //       const key = `[礼物]🎁 #@#${nick}#@#送出 #@#${giftName} #@#x${giftCount}#@#${giftImg || ''}`;
  //       if (!seen.has(key)) {
  //         seen.add(key);
  //         console.log('[观众]', key);
  //         sendSafe('[观众]' + key);
  //       }
  //     }));
  //   });
  //   ob.observe(document.body, { childList: true, subtree: true });
  // }
  function listenGiftPanel() {
    const ob = new MutationObserver(ms => {
      ms.forEach(m => m.addedNodes.forEach(n => {
        if (!n || !n.querySelector) return;

        // 根据新的 HTML 结构调整选择器
        const g = n.closest('.gift-slot-item') || n;
        const nick = g.querySelector('.gift-info h4')?.textContent?.trim();
        const giftName = g.querySelector('.gift-name')?.textContent?.trim();
        const giftImg = g.querySelector('.gift-img img')?.src; // 修改为直接获取 img 的 src
        const giftCount = g.querySelector('.gift-combo span')?.textContent?.trim() || '1'; // 修改为获取 span 的内容

        if (!nick || !giftName) return; // 确保 nick 和 giftName 都有值

        // const key = `[礼物]🎁 #@#${nick}#@#送出#@#${giftName}#@#x${giftCount}#@#${giftImg || ''}`;
        let jsonGift = {"from":"[快手][观众]","source":"礼物","nickName":nick,"giftName":giftName,"giftCount":giftCount,"giftImg":giftImg}
        if (!seen.has(jsonGift)) {
          seen.add(jsonGift);
          console.log(JSON.stringify(jsonGift));
          sendSafe(JSON.stringify(jsonGift));
        }
      }));
    });

    ob.observe(document.body, { childList: true, subtree: true });
  }
  /* 监听某个聊天列表节点 */
  function listenChatList(listNode) {
    let innerOb = new MutationObserver(ms => {
      ms.forEach(m => m.addedNodes.forEach(n => {
        if (n.nodeType !== 1) return;
        const txt = extract(n);
        if (txt) {
          // let jsonChat = {"from":"[快手][观众]","source":"聊天","data":txt}
          console.log(JSON.stringify(txt));
          sendSafe(JSON.stringify(txt));
        }
      }));
    });
    innerOb.observe(listNode, { childList: true, subtree: true });
    console.log('已绑定聊天列表', listNode);
  }

  /* 轮询直到找到聊天列表，并监听其重建 */
  function poll() {
    const root = deepRoot(document.body);
    const list = root.querySelector('.chat-history .wrapper') ||
                 root.querySelector('.chat-list') ||
                 root.querySelector('[class*="chat"] [class*="list"]');
    if (!list) { requestIdleCallback(poll); return; }

    listenChatList(list);
    listenGiftPanel();

    let outerOb = new MutationObserver(() => {
      if (!list.isConnected) {
        console.log('聊天列表被重建，重新监听');
        requestIdleCallback(poll);
      }
    });
    outerOb.observe(list.parentNode, { childList: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => requestIdleCallback(poll));
  } else {
    requestIdleCallback(poll);
  }
})();