// ==UserScript==
// @name         抖音直播 DOM 双通道抓取（教学版）
// @namespace    http://tampermonkey.net/
// @version      2025.06.19
// @description  弹幕 & 礼物 双通道实时抓取（含详细中文注释）
// @match        *://live.douyin.com/*
// @match        *://*.douyin.com/*
// @grant        none
// @require      https://cdn.jsdelivr.net/npm/protobufjs@7/dist/protobuf.min.js
// ==/UserScript==
(function () {
    'use strict';

    /* ------------------ 0. 工具函数 ------------------ */
    console.log('%c[TM] 抖音直播 DOM 双通道抓取（教学版）脚本已启动', 'color:red;font-size:16px');

    // 500 条 FIFO 去重队列
    const FIFO_LEN = 500;
    const fifo = [];
    function pushUnique(key) {
        const idx = fifo.indexOf(key);
        if (idx !== -1) return false;          // 已存在
        if (fifo.length >= FIFO_LEN) fifo.shift(); // 删除最早
        fifo.push(key);
        return true;
    }


    /* ------------------ 2. DOM 抓取部分 ------------------ */
    const $ = sel => document.querySelector(sel);
    const getRoot = el => el && (el.shadowRoot || el);

    /**
     * 从一个聊天节点中提取信息
     * @param {Element} node 带有 data-index 的聊天行
     * @return {Object|null} 返回 {type:'danmu'|'gift', text:'...', url:'...'}
     */
    function txtOf(node) {
        if (!node.matches('[data-index]')) return null;
        const full = node.textContent.trim();
        if (!full) return null;

        // 礼物正则：昵称：送出了 礼物名 ×数字
        const giftMatch = full.match(/^(.*?)：.*?送出了(.*?)\s*×\s*(\d+)$/);
        if (giftMatch) {
            const [, nick, giftName, cnt] = giftMatch;
            const img = node.querySelector('img[src*="webcast"][src$=".png"]');
            const giftUrl = img ? img.src : '';
            return {
                type: 'gift',
                text: `${nick}: ${giftName.trim() || '礼物'}×${cnt}:GiftAddress:${giftUrl}`,
                url: giftUrl
            };
        }

        // 弹幕正则：昵称：内容
        const dmMatch = full.match(/^(.*?)：(.+)$/);
        if (dmMatch) {
            const [, nick, msg] = dmMatch;
            return { type: 'danmu', text: `${nick}: ${msg}` };
        }
        return null;
    }

    /* MutationObserver 用于监听聊天容器 */
    let ob = null, retryT = null, lastMsgT = 0;

    function scan() {
        if (ob) ob.disconnect();
        ob = new MutationObserver(list => {
            for (const m of list) {
                for (const n of m.addedNodes) {
                    if (n.nodeType !== 1) continue;
                    const res = txtOf(n);
                    if (!res) continue;
                    const key = res.text + (res.url || '');
                    if (!pushUnique(key)) continue;

                    // 向上找聊天容器根节点，找到后锁定
                    let p = n.parentElement;
                    while (p && p.children.length < 5) p = p.parentElement;
                    if (p) { lock(p); return; }
                }
            }
        });
        ob.observe(getRoot(document.body) || document.body,
                   { childList: true, subtree: true });
    }

    function lock(container) {
        if (ob) ob.disconnect();
        const root = getRoot(container) || container;

        ob = new MutationObserver(list => {
            lastMsgT = Date.now();
            list.forEach(m => m.addedNodes.forEach(n => {
                const res = txtOf(n);
                if (!res) return;
                const key = res.text + (res.url || '');
                if (!pushUnique(key)) return;
                console.log(`[DOM-${res.type === 'gift' ? '礼物' : '聊天'}]`, res.text);
            }));
        });
        ob.observe(root, { childList: true, subtree: false });
        console.log('[DOM] 已锁定聊天容器');
        resetRetry();
    }

    function resetRetry() {
        clearTimeout(retryT);
        retryT = setTimeout(() => {
            if (Date.now() - lastMsgT > 3000) {
                console.log('[DOM] 3 秒无消息，重新扫描…');
                scan();
            } else resetRetry();
        }, 3100);
    }

    scan();   // 启动 DOM 抓取

})();