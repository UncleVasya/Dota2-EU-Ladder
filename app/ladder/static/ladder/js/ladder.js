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

    // transform time values into 'n days ago'
    $('.timeago').timeago();
};

$(document).ready(main);
