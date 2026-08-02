plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "net.mdir.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "net.mdir.android"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }
}

tasks.withType<Test>().configureEach {
    // `-Dmdirnet.live=1` 은 Gradle JVM 에만 붙는다. 테스트 JVM 으로 넘겨야
    // LiveLanTest 의 assumeTrue 가 통과한다
    for (key in listOf("mdirnet.live", "mdirnet.live.mode")) {
        System.getProperty(key)?.let { systemProperty(key, it) }
    }
    testLogging {
        showStandardStreams = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.documentfile)
    implementation(libs.kotlinx.coroutines.android)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    debugImplementation(libs.androidx.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    // 프레임 헤더는 org.json 으로 다룬다. 프레임워크에 들어있지만 JVM 테스트에는
    // 예외를 던지는 스텁만 있으므로 테스트 클래스패스에 진짜 구현을 넣는다
    testImplementation(libs.json)
}
