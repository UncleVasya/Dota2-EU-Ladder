var main = function() {
    $('table.tablesorter').tablesorter({
        sortList: [[1,0]],  // 2nd column sorted asc
        sortStable: true,
        sortInitialOrder: 'asc'
    });
};

$(document).ready(main);
