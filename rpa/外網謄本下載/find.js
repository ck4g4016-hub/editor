function ExecuteScript() {
    // 在領件區裡找「這一列 Excel 送出去的那一筆」。
    //
    // 不能照順序配：領件區列的是這個帳號當天所有的申請，包括別的案子，
    // 第 N 個待下載的不一定是這一批第 N 個送出去的（承辦人 2026-10-12：
    // 那時候領件區裡還有別的案子，整批檔名跟內容就錯開了）。
    // 所以改成對「申請日期時間」：送件時流程把電腦時間記在 Excel 的 I 欄，
    // 這裡拿來跟領件區每一列的申請時間比。
    //
    // 對不到、或同時對到兩筆，就回報對不到 —— 寧可這一份不下載，
    // 也不要把別人的謄本冠上這個人的文號跟身分證號。
    var all = "%TimesText%".split("|").map(function (s) { return s.trim(); })
                           .filter(function (s) { return s.length > 0; });
    var mine = "%ExcelData_SentTime%".trim();
    var EARLY = 20;      // 秒：系統蓋的申請時間最多比電腦記的早這麼多（送出後頁面載入）
    var LATE = 3;        // 秒：最多晚這麼多（時鐘只有秒、加上量測誤差）
    var TOL = 10;        // 秒：推算模式下，對齊之後最多差這麼多
    var MAX_SKEW = 180;  // 秒：電腦跟系統差超過這麼多，就不相信任何對法

    function excelSec(text) {
        var m = text.match(/(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})/);
        if (!m) return null;
        return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000;
    }
    function siteSec(text) {
        var m = (text || "").match(/^(\d{2,3})\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})$/);
        if (!m) return null;
        return Date.UTC(+m[1] + 1911, +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000;
    }

    // 電腦時間跟系統時間差幾秒：直接問系統現在幾點（回應標頭裡的 Date）。
    // 只送一個 HEAD 給同一個網站，不帶任何資料。問不到就回 null，改用推算。
    //
    // 為什麼一定要量：只用這一批的時間去「推算」差多少，在一種情況下會推錯
    // —— 電腦時間剛好慢了「一筆的送件間隔」，而且領件區前面剛好有一筆別的
    // 案子。那時候「整批錯開一份」跟「完全對齊」看起來一樣合理，推算會挑錯，
    // 錯的方式正好就是這次要修的那種。量得到就不必推算。
    function measureSkew() {
        try {
            var xhr = new XMLHttpRequest();
            var before = Date.now();
            xhr.open("HEAD", location.origin + "/favicon.ico?t=" + before, false);
            xhr.send(null);
            var after = Date.now();
            var server = Date.parse(xhr.getResponseHeader("Date") || "");
            if (isNaN(server)) return null;
            var skew = Math.round((server - (before + after) / 2) / 1000);
            return Math.abs(skew) <= MAX_SKEW ? skew : null;
        } catch (err) {
            return null;
        }
    }

    var rows = document.querySelectorAll(".tab5 tr");
    if (rows.length < 2) return "對不到：領件區的表格是空的或找不到";

    // 欄位位置沿用原本流程確認過的：資料來源是 cells[2]、下載日期時間是
    // td:eq(7)，所以最前面有一欄看不到的，申請日期時間是 cells[1]、收件號是
    // cells[3]。每一列都再驗一次格式，版面改了就整個停下來，不硬猜。
    var page = [];
    for (var i = 1; i < rows.length; i++) {
        var cells = rows[i].querySelectorAll("td");
        var isDeed = false;
        for (var c = 0; c < cells.length; c++) {
            if (cells[c].innerText.indexOf("土地建物登記資料") >= 0) { isDeed = true; break; }
        }
        if (!isDeed) continue;
        if (cells.length < 8) return "對不到：領件區第 " + i + " 列的欄位數跟預期不一樣，表格版面可能改了";
        var t = siteSec(cells[1].innerText.trim());
        if (t === null) return "對不到：領件區第 " + i + " 列的第一欄不是申請日期時間，表格版面可能改了";
        page.push({ dom: i, t: t, no: cells[3].innerText.trim(), done: cells[7].innerText.trim() !== "" });
    }
    if (page.length === 0) return "對不到：領件區裡沒有土地建物登記資料";

    var sent = [];
    for (var k = 0; k < all.length; k++) {
        var s0 = excelSec(all[k]);
        if (s0 === null) return "對不到：Excel 的送件時間格式不對（" + all[k] + "）";
        sent.push(s0);
    }
    var me = excelSec(mine);
    if (me === null) return "對不到：這一列沒有送件時間";

    var how, inWindow;
    var direct = measureSkew();
    if (direct !== null) {
        how = "對時";
        inWindow = function (p, e) {
            var d = p.t - (e + direct);
            return d >= -EARLY && d <= LATE;
        };
    } else {
        // 問不到系統時間：用這一批的時間推算差多少。這一條路比較嚴：
        // 只要有另一種差很多的對法一樣合理，就整批不認。
        how = "推算";
        var skews = [];
        for (var a = 0; a < sent.length; a++) {
            for (var b = 0; b < page.length; b++) {
                var dd = page[b].t - sent[a];
                if (Math.abs(dd) <= MAX_SKEW) skews.push(dd);
            }
        }
        if (skews.length === 0) return "對不到：領件區裡沒有這一批的申請（時間都對不上，可能在下一頁、或不是今天送的）";
        var scoreOf = function (skew) {
            var n = 0;
            for (var j = 0; j < sent.length; j++) {
                var cnt = 0;
                for (var q = 0; q < page.length; q++) { if (Math.abs(page[q].t - (sent[j] + skew)) <= TOL) cnt++; }
                if (cnt === 1) n++;
            }
            return n;
        };
        var best = null, bestScore = -1;
        for (var x = 0; x < skews.length; x++) {
            var sc = scoreOf(skews[x]);
            if (sc > bestScore || (sc === bestScore && Math.abs(skews[x]) < Math.abs(best))) {
                best = skews[x]; bestScore = sc;
            }
        }
        for (var y = 0; y < skews.length; y++) {
            if (Math.abs(skews[y] - best) > TOL && scoreOf(skews[y]) === bestScore) {
                return "對不到：問不到系統時間，而這一批的送件時間有兩種對法一樣合理，分不出來";
            }
        }
        inWindow = function (p, e) { return Math.abs(p.t - (e + best)) <= TOL; };
    }

    var hits = page.filter(function (p) { return inWindow(p, me); });
    if (hits.length === 0) return "對不到：領件區裡找不到這一列送出去的那一筆（" + how + "）";
    if (hits.length > 1) return "對不到：這一列送件時間附近有 " + hits.length + " 筆申請，分不出是哪一筆（" + how + "）";
    var hit = hits[0];
    // 一對一：對到的這一筆，不可以同時也對得上這一批的別列
    for (var z = 0; z < sent.length; z++) {
        if (sent[z] !== me && inWindow(hit, sent[z])) {
            return "對不到：對到的那一筆同時也對得上別列，分不出來（" + how + "）";
        }
    }
    return (hit.done ? "已下載" : "下載") + ":" + hit.dom + ":" + hit.no + ":" + how;
}
