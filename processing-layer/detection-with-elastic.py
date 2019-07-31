"""
run the example
	spark-submit --master spark://master:7077  --jars /opt/spark/jars/elasticsearch-spark-20_2.10-5.5.1.jar,/opt/spark/jars/spark-streaming-kafka-0-8-assembly_2.11-2.1.1.jar --conf spark.executor.extraJavaOptions=" -XX:MaxPermSize=15G "  /tmp/new.py hdfs://master:9000/user/app/reduced-25-with-classes.out 10.10.10.3:2181 topic1
"""
from __future__ import print_function

import sys

from pyspark.mllib.linalg import Vectors
from pyspark import SparkContext
from pyspark.streaming import StreamingContext
from pyspark.streaming.kafka import KafkaUtils, OffsetRange
import json
import time
from geoip import geolite2

#### ML
from pyspark.mllib.tree import DecisionTree, DecisionTreeModel
from pyspark.mllib.util import MLUtils
####

####
import numpy as np 
from pyspark.mllib.stat import Statistics
from pyspark.mllib.regression import LabeledPoint
from pyspark.mllib.util import MLUtils
from tempfile import NamedTemporaryFile
####

from py4j.protocol import Py4JJavaError
import os
import subprocess

####for firewall
import httplib
import json

numberFeatures=46 #dataset Antonio=25 dataset com=41
#ipFirewall='10.240.114.45'
ipES="127.0.0.1"
#31'
numberClasses=2 #for dataset Antonio (0=Normal, 1=DoS, 2=Probe (3 classes)) #renato 0=Normal 1=Alerta (2 classes)

def convertTofloat(x):
	for i in range(len(x)):
			x[i]=float(x[i])
	return x

def convertToString(x):
	for i in range(len(x)):
		x[i]=str(x[i])
	return x

def dataPreparing(lines):

	virgulas  = lines.map(lambda x: x.split(',')).map(lambda x:(json.dumps(x[0:4]), x[4:numberFeatures]))

	vectors = virgulas.mapValues(lambda x: np.array(x)) #converting values to arrays
	test = vectors.map(lambda x:x[1]) #taking only the values
	classes = test.map(lambda x:x[numberFeatures-5]) #get the class
	#classes=classes.map(lambda x: '1' if x !='0' else '0') # passing to binary classes
	classes=classes.map(lambda x: '1' if x != u'"attack"' else '0')
	test = test.map(lambda x:x[0:numberFeatures-5]) #removing the class
	
	return test, classes

def CorrelationFeature(vectors):
	
	matriz=sc.broadcast(Statistics.corr(vectors, method="pearson"))

	summary = Statistics.colStats(vectors)

	varianza=summary.variance()

	#########new heuristic diogo proposal
	w={}
	aij={}
	for i in range(len(matriz.value)):
		w[i]=0
		aij[i]=0
		for j in np.nan_to_num(matriz.value[i]):
			k=abs(j)
			aij[i]=aij[i]+k
		w[i]=varianza[i]/aij[i]

	r=sorted([(value,key) for (key,value) in w.items()],reverse=True) #features sorted

	index=[]
	for i in r:
		index.append(i[1])
	
	index=index[0:6] #tacking the first 6 features

	return index

def MatrixReducerStream(vectors, index):

	reducedMatrix =[]
	#####
	vectors = np.matrix(vectors)

	for k in index:
		#reducedMatrix.append(matrizRaw[:,k[1]]) #reduced matrix 
		reducedMatrix.append(vectors[:,k]) #reduced matrix 

	vectors2 = np.column_stack(reducedMatrix)
	vectors2 = np.array(vectors2)
	
	return vectors2

def MatrixReducer(vectors,index):

	def takeElement(vector):
		p=[]
		for i in index:
			p.append(vector[i])
		return p
	
	reducedMatrix= vectors.map(lambda x: takeElement(x))

	vectors2=reducedMatrix.map(lambda x: np.column_stack(x))

	return vectors2 #matriz reducida

def pass2libsvm(vectors2,classes):
	newVector=classes.zip(vectors2)
	grouped=newVector.groupByKey().mapValues(list)
	final=newVector.map(lambda x : LabeledPoint(x[0],x[1]))

	return final

def path_exist(file): 
	path='hdfs://master:9000/user/app/'
	try:
		cmd = ['hdfs', 'dfs', '-find',path]
		files = subprocess.check_output(cmd).strip().split('\n')
		if file in files:
			return True
		else:
			return False
	except Py4JJavaError as e:
		return False		

def getModel(path,file):
	
	if path_exist(path+'index-'+file):
		index=sc.textFile(path+'index-'+file)
		a=index.collect()
		b=lambda x : [ int(i) for i in x ]
		
		return DecisionTreeModel.load(sc, path+'model-'+file), b(a)

	else:
		vector,classes = dataPreparing(sc.textFile(path+file))

		index=CorrelationFeature(vector) #se precisar de feature do Feature Selection

		reduced=MatrixReducer(vector,index) 

		data=pass2libsvm(reduced,classes) 

		# Train a DecisionTree model.
		# Empty categoricalFeaturesInfo indicates all features are continuous.
		
		model = DecisionTree.trainClassifier(data, numberClasses, {})	 #, maxDepth=5, maxBins=32)

		model.save(sc, path+'model-'+file)			

		return	model, index

def addLocation(x):
	from geoip import geolite2
 	dictX = dict(x)
 	locSrcIp = geolite2.lookup(dictX['srcip'])
 	locDstIp = geolite2.lookup(dictX['dstip'])
	
 	try:
 		if locSrcIp and locSrcIp.location:

 			dictX['srclocation']= {'lat': locSrcIp.location[0], 'lon':locSrcIp.location[1]}
 		else:
 			dictX['srclocation']= {'lat': 48, 'lon':22}


 		if locDstIp and locDstIp.location:
 			dictX['dstlocation']= {'lat': locSrcIp.location[0], 'lon':locSrcIp.location[1]}
 		else:
 			dictX['dstlocation']= {'lat': 48, 'lon':22}

 	except AttributeError:
 		pass

 	except TypeError:
 		pass
	
 	return dictX

if __name__ == "__main__":

	if len(sys.argv) != 4:
		print("Usage: kafka_wordcount.py <file> <hdfs-files> <zk> <topic> ", file=sys.stderr)
		exit(-1)

	sc = SparkContext(appName="Kafka with DT")
	sc.setLogLevel("ERROR")
	
	#Create model
	a=0
	orig=sys.argv[1]
	path='hdfs://master:9000/user/app/'
	file=orig.split('app/')[1]
	features=sc.textFile(path+'features-des.txt').collect()
	feat=[]
	for i in features:
		feat.append(i.split(',')[1])

	[model,index]=getModel(path,file)
	
	if path_exist(path+'index-'+file) == False: #('hdfs://master:9000/user/app/index-25-reduced.txt') == False:
		rdd=sc.parallelize(index)
		rdd.saveAsTextFile(path+'index-'+file) #('hdfs://master:9000/user/app/index-25-reduced.txt')

	####Streaming
	ssc = StreamingContext(sc, 1)

	###kafka
	zkQuorum, topic = sys.argv[2:]

	kvs = KafkaUtils.createDirectStream(ssc, [topic], {"metadata.broker.list": zkQuorum})
	parsed = kvs.map(lambda v: json.loads(v[1]))

	print()
	print(parsed)
	print()

	lines  = parsed.map(lambda x: x.split(',')).map(lambda x:(json.dumps(x[0:4]), x[4:numberFeatures-1])).mapValues(lambda x: convertTofloat(x))
 	elastic= parsed.map(lambda x: x.split(',')).map(lambda x: {feat[i]: x[i] for i in range(numberFeatures-2)}).map(addLocation) #get the whole vector

 	test = lines.flatMapValues(lambda x: MatrixReducerStream(x,index))

	conf = {"es.resource" : "spark/test", "es.nodes" : ipES, "es.index.auto.create": "true"}
		   
	vec = test.mapValues(Vectors.dense) #now we have the vectors with the format of the ML

	try:
		vec=test.map(lambda x: x[1])
		ips=test.transform(lambda x: x.keys().zipWithIndex()).map(lambda x: (x[1],x[0]))
		algo=test.transform(lambda x: model.predict(x.values()).zipWithIndex()).map(lambda x: (x[1],x[0]))

		joined = ips.join(algo).transform(lambda x: x.values())
		joined.foreachRDD(lambda v: print(v.collect()))
		
		yyy=elastic.transform(lambda x: x.zipWithIndex()).map(lambda x: (x[1],x[0]))

		toElastic = yyy.join(algo).transform(lambda x: x.values())
		
		almostSend=toElastic.map(lambda x: dict([i for i in x[0].items()+[('predict',x[1]),('timestamp',int(time.time()*1000))]]))
		
		now=almostSend.map(lambda x: ('key',x))
		now.foreachRDD(lambda v: print(v.collect()))
		
		now.foreachRDD(lambda x: x.saveAsNewAPIHadoopFile(path='-',outputFormatClass="org.elasticsearch.hadoop.mr.EsOutputFormat",keyClass="org.apache.hadoop.io.NullWritable",valueClass="org.elasticsearch.hadoop.mr.LinkedMapWritable",conf=conf))
	
	except AttributeError:
		pass

	ssc.start()
	ssc.awaitTermination()