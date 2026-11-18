(() => {
  const recipesEl = document.getElementById("recipes-data");
  if (!recipesEl) return; // not on index page

  const recipes = JSON.parse(recipesEl.textContent);
  const categoryCheckboxes = document.querySelectorAll('#category-list input[data-filter="category"]');
  const ingredientSearch = document.getElementById('ingredient-search');
  const clearBtn = document.getElementById('clear-filters');
  const cards = document.querySelectorAll('.recipe-card');
  const countEl = document.getElementById('result-count');
  const total = cards.length;

  function getSelectedCategories() {
    return Array.from(categoryCheckboxes)
      .filter(cb => cb.checked)
      .map(cb => cb.value);
  }

  function matchesIngredient(card, term) {
    if (!term) return true;
    const id = card.dataset.id;
    const recipe = recipes.find(r => r.id === id);
    if (!recipe) return true;
    const blob = [recipe.title, recipe.description, ...recipe.ingredients].join(" ");
    return blob.toLowerCase().includes(term.toLowerCase());
  }

  function filter() {
    const selected = getSelectedCategories();
    const term = ingredientSearch.value.trim();
    let showing = 0;

    cards.forEach(card => {
      const catMatch = selected.length === 0 || selected.includes(card.dataset.category);
      const ingMatch = matchesIngredient(card, term);
      const show = catMatch && ingMatch;
      card.style.display = show ? "" : "none";
      if (show) showing++;
    });

    countEl.textContent = `${showing} of ${total} 레시피`;
  }

  categoryCheckboxes.forEach(cb => cb.addEventListener("change", filter));
  ingredientSearch.addEventListener("input", filter);
  clearBtn.addEventListener("click", () => {
    categoryCheckboxes.forEach(cb => (cb.checked = false));
    ingredientSearch.value = "";
    filter();
  });

  filter();
})();
