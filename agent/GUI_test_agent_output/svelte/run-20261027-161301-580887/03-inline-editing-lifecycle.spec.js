const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should support editing a todo', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Double click to enter edit mode, change text, and save or cancel.' },
    { type: 'expected', description: 'Double click adds "editing" class and reveals input. Enter saves changes. Escape reverts changes.' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Original');
  await input.press('Enter');

  const todoItem = page.locator('.todo-list li').nth(0);
  const label = todoItem.locator('label');

  // Start editing
  await label.dblclick();
  await expect(todoItem).toHaveClass(/editing/);
  
  const editInput = todoItem.getByRole('textbox', { name: 'Edit todo' });
  await editInput.fill('Edited Value');
  await editInput.press('Enter');

  await expect(todoItem).not.toHaveClass(/editing/);
  await expect(label).toHaveText('Edited Value');

  // Cancel editing
  await label.dblclick();
  await editInput.fill('Canceled Change');
  await editInput.press('Escape');
  await expect(todoItem).not.toHaveClass(/editing/);
  await expect(label).toHaveText('Edited Value');

  // Delete on empty edit
  await label.dblclick();
  await editInput.fill('');
  await editInput.press('Enter');
  await expect(page.locator('.todo-list li')).toHaveCount(0);
});
