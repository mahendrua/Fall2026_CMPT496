const { test, expect } = require('@playwright/test');
test.beforeEach(async ({ page }) => {
    await page.goto(process.env.BASE_URL);
    await expect(page.getByRole('button', { name: 'Increase', exact: true })).toBeVisible();
});
test('counter adds one', async ({ page }) => {
    test.info().annotations.push({ type: 'expected', description: 'Increase changes zero to one, as the UI states.' });
    await page.getByRole('button', { name: 'Increase', exact: true }).click();
    await expect(page.locator('#count')).toHaveText('1');
});
test('theme toggles', async ({ page }) => {
    await page.getByRole('button', { name: 'Theme', exact: true }).click();
    await expect(page.locator('body')).toHaveClass('dark');
});
test('details toggle', async ({ page }) => {
    await page.getByRole('button', { name: 'Show/Hide', exact: true }).click();
    await expect(page.locator('#details')).toBeVisible();
    await page.getByRole('button', { name: 'Show/Hide', exact: true }).click();
    await expect(page.locator('#details')).toBeHidden();
});
test('notes persist across reload', async ({ page }) => {
    await expect(page.getByLabel('Notes')).toHaveValue('');
    await page.getByLabel('Notes').fill('independent note');
    await page.getByRole('button', { name: 'Save notes', exact: true }).click();
    await expect(page.locator('#saved')).toHaveText('Saved');
    await page.reload();
    await expect(page.getByLabel('Notes')).toHaveValue('independent note');
});
test('next test starts with isolated storage', async ({ page }) => {
    await expect(page.getByLabel('Notes')).toHaveValue('');
    await expect(page.locator('#count')).toHaveText('0');
});
