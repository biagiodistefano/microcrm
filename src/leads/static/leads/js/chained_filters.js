"use strict";
{
  const $ = django.jQuery;

  $(function () {
    const $country = $('select[name="country"]');
    const $city = $('select[name="city__id__exact"]');

    if (!$country.length || !$city.length) return;

    $country.on("change", function () {
      const country = $(this).val();
      const url = document.getElementById("chained-filters-url").dataset.url;

      $.getJSON(url, { country: country }, function (data) {
        // Preserve current city selection if still valid
        const currentCity = $city.val();

        // Destroy Select2 before manipulating options
        if ($city.hasClass("select2-hidden-accessible")) {
          $city.select2("destroy");
        }

        $city.empty().append($("<option>", { value: "", text: "---------" }));
        $.each(data.cities, function (_, city) {
          $city.append($("<option>", { value: city.id, text: city.label }));
        });

        // Restore selection if the city is still in the list, otherwise reset
        if (currentCity && $city.find('option[value="' + currentCity + '"]').length) {
          $city.val(currentCity);
        } else {
          $city.val("");
        }

        // Re-init Select2
        $city.djangoCustomSelect2();
      });
    });
  });
}
