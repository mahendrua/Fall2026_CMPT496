import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should update todo on Enter and cancel on Escape', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Tests startEdit, handleEdit, and updateItem handlers' },
    { type: 'expected', description: 'Text updates on Enter/Blur and reverts/cancels on Escape' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Initial Task');
  await input.press('Enter');

  const label = page.locator('.todo-list li label');
  await label.dblclick();

  const editInput = page.getByLabel('Edit todo');
  await expect(editInput).toBeFocused();
  
  // Test Escape cancellation
  await editInput.fill('Changed Task');
  await editInput.press('Escape');
  await expect(label).toHaveText('Initial Task');

  // Test Enter saving
  await label.dblclick();
  await editInput.fill('Updated Task');
  await editInput.press('Enter');
  await expect(label).toHaveText('Updated Task');

  // Test Blur saving (updateItem logic)
  await label.dblclick();
  await editInput.fill('Blurred Update');
  await page.mouse.click(0, 0); // Click outside to blur
  await expect(label).toHaveText('Blurred Update');
});

test('should remove todo if edited to empty string', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Tests updateItem empty value behavior' },
    { type: 'expected', description: 'Todo is removed if the description becomes empty' }
  );

  await page.getByPlaceholder('What needs to be done?').fill('To be deleted');
  await page.getByPlaceholder('What needs to be done?').press('Enter');

  await page.locator('.todo-list li label').dblclick();
  const editInput = page.getByLabel('Edit todo');
  await editInput.fill('');
  await editInput.press('Enter');

  await expect(page.locator('.todo-list li')).toHaveCount(0);
});
