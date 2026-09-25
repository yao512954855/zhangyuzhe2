/**
 * `qrcode` 缺类型声明 —— WP-WEB-03 补充
 *
 * `qrcode@1` 包本体不带 `types`/`typings` 字段，也没有随包的 `.d.ts`，
 * 而项目的 `tsconfig.json` 开着 `strict`，未声明模块会直接报
 * `TS7016: Could not find a declaration file for module 'qrcode'`。
 *
 * 两种常见修法：
 *   1. `npm i -D @types/qrcode` —— 但会改动 `package.json`（受保护文件，本包禁止改）；
 *   2. 自己写一份**最小**ambient 声明（就是本文件）—— 零依赖、不动别人的文件。
 *
 * 这里只声明本项目实际用到的 API（`toCanvas`），保持最小可维护面：
 * 一旦上游补了官方类型，删掉本文件即可，业务代码无需改动。
 */
declare module 'qrcode' {
  /** `QRCode.toCanvas(canvas, text, options)` 的选项（只列我们用到的字段）。 */
  export interface QRCodeRenderOptions {
    /** 输出边长（px）。 */
    width?: number
    /** 静默区（模块数）。 */
    margin?: number
    /** 前景/背景色，`dark` 为点阵色、`light` 为底色。 */
    color?: { dark?: string; light?: string }
    /** 纠错级别，越高越耐污损但码点越密。 */
    errorCorrectionLevel?: 'L' | 'M' | 'Q' | 'H'
  }

  /** 把 `text` 渲染到已有 `<canvas>` 上。 */
  export function toCanvas(
    canvas: HTMLCanvasElement,
    text: string,
    options?: QRCodeRenderOptions,
  ): Promise<void>

  /** 渲染成 data URL（备用，当前未使用但保留以免后续补功能时又要改声明）。 */
  export function toDataURL(text: string, options?: QRCodeRenderOptions): Promise<string>

  const QRCode: {
    toCanvas: typeof toCanvas
    toDataURL: typeof toDataURL
  }

  export default QRCode
}
