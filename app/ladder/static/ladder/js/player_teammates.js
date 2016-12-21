var main = function() {
    $('table.tablesorter').tablesorter({
        sortList: [[3,1]],  // 3rd column sorted desc
        sortStable: true,
        sortInitialOrder: 'desc'
    });
};

$(document).ready(main);
