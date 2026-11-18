function ExecuteScript() {
    // 找到的結果長這樣：下載:<列>:<收件號>:<對時或推算>
    var parts = "%FindResult%".split(":");
    var n = parseInt(parts[1], 10);
    var rows = document.querySelectorAll(".tab5 tr");
    if (isNaN(n) || n <= 0 || n >= rows.length) return "失敗：列號不對";
    var cells = rows[n].querySelectorAll("td");
    // 點之前再確認一次是同一筆：中間頁面重新整理過的話，列號可能已經換人了
    if (cells.length < 4 || cells[3].innerText.trim() !== parts[2]) {
        return "失敗：這一列的收件號跟剛才對到的不一樣";
    }
    var button = rows[n].querySelector("td > div > a[onclick*='REG']");
    if (!button) return "失敗：找不到查詢按鈕";
    button.click();
    return "已點開：" + parts[2];
}
