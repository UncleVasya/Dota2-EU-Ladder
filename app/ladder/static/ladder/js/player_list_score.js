var main = function() {
    $('table.tablesorter').tablesorter({
        sortList: [[1,1]],  // 2nd column sorted desc
        sortStable: true,
        sortInitialOrder: 'desc'
    });
};

$(document).ready(main);
