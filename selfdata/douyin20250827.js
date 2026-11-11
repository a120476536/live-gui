// ==UserScript==
// @name         抖音直播 DOM 双监控【调试版+HTTP】
// @namespace    http://tampermonkey.net/
// @version      2025.06.19-http
// @description  DOM监控 + HTTP转发
// @match        *://live.douyin.com/*
// @match        *://*.douyin.com/*
// @connect      localhost
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// ==/UserScript==

(() => {
  'use strict';

  /* ---------- 0. 日志工具 ---------- */
  const log = (...a) => console.log('[DEBUG]', ...a);

  /* ---------- 1. HTTP 发送 ---------- */
  const httpUrl = "http://127.0.0.1:8766"; // 修改为你服务端监听的地址
  const sendQueue = [];
  let sending = false;
  const SEND_INTERVAL = 100; // ms

  function queueSend(data) {
    sendQueue.push(data);
    if (!sending) startQueueSend();
}

function startQueueSend() {
    sending = true;
    const timer = setInterval(() => {
        if (sendQueue.length === 0) {
            clearInterval(timer);
            sending = false;
            return;
        }
        const item = sendQueue.shift();
        sendHttp(item);
    }, SEND_INTERVAL);
}
  function sendHttp(data) {
      try {
          GM_xmlhttpRequest({
              method: "POST",
              url: httpUrl,
              headers: { "Content-Type": "application/json" },
              data: JSON.stringify(data),
              onload: function(res) {
                  console.log('[HTTP] 已发送 →', res.responseText);
              },
              onerror: function(err) {
                  console.log('[HTTP] 发送失败 →', err);
              }
          });
      } catch (err) {
          console.log('[HTTP] 请求异常 →', err);
      }
 }

  /* ---------- 2. 解析聊天 ---------- */
  function parseChat(node) {
      const full = node.textContent.trim();
      let nickName, content, jsonChat = null;

      if (full.includes(':')) {
          let fullDatas = full.split(":");
          nickName = fullDatas[0];
          content = fullDatas[1];
          if (nickName && nickName.trim() !== "" && content && content.trim() !== "") {
              jsonChat = { "from": "[抖音][观众]", "source": "聊天","nickName": nickName,"msg": content };
          }
      } else {
          if (full && full.trim() !== "") {
              jsonChat = { "from": "[抖音][观众]", "source": "聊天", "nickName": full, "msg": full };
          }
      }
      return jsonChat;
  }

  /* ---------- 3. 解析弹幕 ---------- */
  function parseDanmu(danmu) {
      // 礼物
      /*const img = danmu.querySelector('img[src*="webcast/"][src$=".png"]');
      if (img) {
          const spans = [...img.parentElement.children]
              .filter(el => el.tagName === 'SPAN')
              .map(el => el.textContent.trim());

          let nickName = spans[0] || '';
          let giftName = spans.slice(1, -2).join(' ');
          const count = spans.at(-1) || '1';

          if (/\s/.test(nickName)) {
              [nickName, giftName] = nickName.trim().split(/\s+/);
          }

          return {
              from: "[抖音][观众]",
              source: "礼物",
              nick: nickName,
              giftName: giftName,
              giftCount: count,
              giftImg: img.src
          };
      }*/
     const img = danmu.querySelector('img[src*="webcast/"][src$=".png"]');
    if (img) {
      const spans = [...img.parentElement.children]
        .filter(el => el.tagName === 'SPAN')
        .map(el => el.textContent.trim());
       log('【礼物 spans】', spans);
      let nickName;
      let giftName;
      let dataUserGif  = spans[0] || '';
      nickName = dataUserGif;
      giftName = dataUserGif;
      const hasSpace = /\s/.test(dataUserGif);   // true 表示包含空格（或 Tab、换行等空白字符）
      if(hasSpace){
        const dataUserGifs = dataUserGif.trim().split(/\s+/);
        nickName = dataUserGifs[0];
        giftName = dataUserGifs[1];
      }
      const gift  = spans.slice(1, -2).join(' ');
      const count = spans.at(-1) || '1';
      // return { type: 'gift', source: '弹幕', user, gift, count };
       let jsonGift = {"from":"[抖音][观众]","source":"礼物","nickName":nickName, "giftName":giftName,"count":count,"gifImg":img.src}
       return jsonGift;
    }
      // 普通弹幕
      const txt = danmu.querySelector('span[class*="content-with-emoji-text"]');
      if (txt) {
          return { from: "[抖音][观众]", source: "弹幕聊天", nickName: "", msg: txt.textContent.trim() };
      }
      return null;
  }

  /* ---------- 4. 监听聊天 ---------- */
  function watchChat() {
      const ob = new MutationObserver(list => {
          list.forEach(m => m.addedNodes.forEach(n => {
              if (n.nodeType !== 1) return;
              if (!n.matches?.('[data-index]')) return;

              const res = parseChat(n);
              if (res) {
                  log('【聊天结果】', JSON.stringify(res));
                  // sendHttp(res);
                  queueSend(res);
              }
          }));
      });
      ob.observe(document.body, { childList: true, subtree: true });
      log('[聊天] 监听已挂');
  }

  /* ---------- 5. 监听弹幕 ---------- */
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
              const res = parseDanmu(n) || (n.querySelector && parseDanmu(n.querySelector('.danmu')));
              if (res) {
                  log('【弹幕结果】', JSON.stringify(res));
                  // sendHttp(res);
                  queueSend(res);
              }
          }));
      });
      ob.observe(root, { childList: true, subtree: true });
      log('[弹幕] 监听已挂 →', root);
  }

  /* ---------- 6. 启动 ---------- */
  if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => { watchChat(); watchDanmu(); });
  } else {
      watchChat(); watchDanmu();
  }

})();
