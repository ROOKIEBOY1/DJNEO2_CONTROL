import { router } from '@kit.ArkUI';
import { SelfTest, SelfTestResult, TestItem } from '../control/SelfTest';

@Entry
@Component
struct Selftest {
  @State result: SelfTestResult = new SelfTestResult();
  @State done: boolean = false;

  aboutToAppear(): void {
    this.run();
  }

  run(): void {
    this.result = SelfTest.run();
    this.done = true;
  }

  build() {
    Column() {
      Row() {
        Text('协议层自检')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .fontColor('#0F2A45')
          .layoutWeight(1)
        Button('返回')
          .fontSize(13)
          .backgroundColor('#EEF3F9')
          .fontColor('#1F5C8B')
          .onClick(() => {
            router.back();
          })
      }
      .width('100%')
      .margin({ top: 8, bottom: 8 })

      Text(this.done
        ? ('通过 ' + this.result.passCount().toString() + ' / ' + this.result.items.length.toString())
        : '运行中…')
        .fontSize(14)
        .fontColor(this.result.allPass() ? '#2E8B7A' : '#B3261E')
        .width('100%')
        .margin({ bottom: 12 })

      List() {
        ForEach(this.result.items, (item: TestItem) => {
          ListItem() {
            Column() {
              Row() {
                Text(item.pass ? '✓' : '✗')
                  .fontSize(15)
                  .fontColor(item.pass ? '#2E8B7A' : '#B3261E')
                  .width(20)
                Text(item.name)
                  .fontSize(14)
                  .fontColor('#23384D')
                  .layoutWeight(1)
              }
              .width('100%')
              Text(item.detail)
                .fontSize(11)
                .fontColor('#7A8BA0')
                .width('100%')
                .margin({ left: 20, top: 2 })
            }
            .width('100%')
            .alignItems(HorizontalAlign.Start)
            .padding({ top: 8, bottom: 8 })
          }
        }, (item: TestItem) => item.name)
      }
      .layoutWeight(1)
      .width('100%')

      Button('重新运行')
        .width('100%')
        .backgroundColor('#1F5C8B')
        .margin({ bottom: 16 })
        .onClick(() => {
          this.run();
        })
    }
    .width('100%')
    .height('100%')
    .padding({ left: 20, right: 20, top: 8 })
    .backgroundColor('#FFFFFF')
  }
}
