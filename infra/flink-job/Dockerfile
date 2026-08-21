FROM maven:3.9.11-eclipse-temurin-17 AS builder
WORKDIR /workspace
COPY pom.xml ./
RUN mvn --batch-mode --no-transfer-progress dependency:go-offline
COPY src ./src
RUN mvn --batch-mode --no-transfer-progress -DskipTests package

FROM flink:1.20.3-scala_2.12-java17
COPY --from=builder /workspace/target/ashare-stream-job-0.3.0-all.jar /opt/flink/usrlib/ashare-stream-job.jar
