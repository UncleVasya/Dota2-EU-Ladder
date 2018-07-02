var main = function() {
    // clickable table rows
    $('[data-link]').click(function(c) {
        var link = $(this).attr('data-link');
        if (c.which === 1) {
            if (c.shiftKey || c.altKey || c.metaKey || c.ctrlKey) {
                window.open(link);
            } else {
                window.location = link;
            }
        }
    });

    $('[data-toggle="tooltip"]').tooltip();

    // mmr correlation tooltips
    $('[data-mmr]').each(function(index) {
        var mmr = $(this).data('mmr');
        $(this).prop('title', 'Correlation: ' + Math.round(ladderToDotaMMR(mmr)) + ' MMR');
    });
};

function ladderToDotaMMR(mmr) {
    var avg_mmr = 4000;

    return avg_mmr - (200 - mmr) * 1000 / 30;
}

$(document).ready(main);
