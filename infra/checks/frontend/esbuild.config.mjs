import * as esbuild from 'esbuild';

const isWatch = process.argv.includes('--watch');

const opts = {
  entryPoints: ['src/plugin.ts'],
  bundle: true,
  outfile: 'dist/spdk-checks.js',
  format: 'iife',
  target: 'es2020',
  minify: !isWatch,
  sourcemap: isWatch,
};

if (isWatch) {
  const context = await esbuild.context(opts);
  await context.watch();
  console.log('Watching for changes...');
} else {
  await esbuild.build(opts);
  console.log('Built dist/spdk-checks.js');
}
