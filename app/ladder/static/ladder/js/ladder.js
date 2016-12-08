var main = function() {
    // transform time values into 'n days ago'
    $('.timeago').timeago();

    $('table.tablesorter').tablesorter({
        sortList: [[2,1]],  // 3rd column sorted desc
        sortStable: true
    });
};

$(document).ready(main);
