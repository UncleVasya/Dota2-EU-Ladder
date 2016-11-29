var main = function() {
    var btn_copy = $('#button-copy');

    btn_copy.tooltip({
        title: 'Copied!',
        trigger: 'manual'
    });

    btn_copy.mouseleave(function(e) {
        $(this).tooltip('hide');
    });

    var clipboard = new Clipboard('#button-copy');
    clipboard.on('success', function(e) {
        btn_copy.tooltip('show');
    });
};

$(document).ready(main);
